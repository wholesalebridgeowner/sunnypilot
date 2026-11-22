#!/usr/bin/env python3
"""Drive Advisor Railway service.

Receives signed events from the Comma agent, stores what each drive sends, and
sends a report about an hour after the last drive. It measures and reports; it
suggests nothing. No rule in it turns a measured number into advice.

The owner CLI can create a pending settings request using the private service
store. An explicit approval tap releases it to the parked agent. Automatic
advisor suggestions remain locked.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from urllib.parse import urlsplit

from approval import APPROVAL_TTL_S, OWNER_RECEIPT_STATUSES, sign_approval
from owner_settings import validate_owner_change
from report_body import report_lines
from settings_guard import GuardError
from wire import WireError, verify_request

NIGHTLY_DELAY_S = 3600
SUGGESTION_WINDOW_S = {"live": 30 * 60, "nightly": 12 * 3600, "owner": 12 * 3600}
SEEN_EVENTS_KEPT = 2000
TITLE_REPORT = "Ram drive report"
REPORT_CLOSING_LINE = "No changes are suggested. This report only measures."
STATUS_FIELDS = ("w", "n", "windowS", "steeringS", "movingS", "warnEvents")     # of a drive's newest summary

# Lock 1 of the suggestion path. A code constant: no constructor argument,
# environment variable, file or request can open it. While it is False the
# service creates, approves and delivers no advisor-originated suggestion.
SUGGESTIONS_BUILT_IN = False


class AdvisorService:
    def __init__(self, store, notifier, key: bytes, public_url: str, *, clock=time.time):
        self.store = store
        self.notifier = notifier
        self.key = key
        self.public_url = public_url.rstrip("/")
        self.clock = clock
        self._confirmed_report = None  # retain an acknowledgement if the first store write must retry

    # --- records -----------------------------------------------------------
    def _state(self) -> dict:
        return self.store.get_json("state.json") or {"seen": [], "approved": [], "nightlyDue": None,
                                                     "nightlyDrives": []}

    def _drive(self, drive_id: str) -> dict:
        """One drive: what drive_start, drive_ready and drive_end said, every summary and every other record."""
        defaults = {
            "driveId": drive_id, "start": None, "ready": None, "summaries": [], "events": [], "end": None,
            "ended": False, "restore": None, "trial": None, "declinedReasons": []}
        return {**defaults, **(self.store.get_json(f"drives/{drive_id}.json") or {})}

    def _report_state(self) -> dict:
        """Adopt the last known unfinished drive from the old end-only scheduler."""
        state = self._state()
        if state.get("nightlyTracking") != 2:
            pending = state.setdefault("nightlyDrives", [])
            last = state.get("lastDriveId")
            if last and last not in pending:
                drive = self._drive(last)
                if not drive.get("ended") and drive.get("reportedAt") is None:
                    pending.append(last)
            if pending and state.get("nightlyDue") is None:
                state["nightlyDue"] = state.get("lastEventAt", self.clock()) + NIGHTLY_DELAY_S
            state["nightlyTracking"] = 2
            self.store.put_json("state.json", state)
        return state

    def _save_drive(self, drive: dict) -> None:
        self.store.put_json(f"drives/{drive['driveId']}.json", drive)

    def _suggestion(self, sid: str) -> dict | None:
        record = self.store.get_json(f"suggestions/{sid}.json")
        if record and record.get("mode") == "owner" and record["status"] in ("pending", "approved"):
            # The immutable grant is the authority for a successful tap.
            grant = self.store.get_json(f"owner-grants/{sid}.json")
            record.update(status="approved" if grant is not None else "pending", approval=grant)
        return record

    def _save_suggestion(self, suggestion: dict) -> None:
        self.store.put_json(f"suggestions/{suggestion['id']}.json", suggestion)

    # --- events from the Comma ---------------------------------------------
    def handle_event(self, event: dict) -> dict:
        if not isinstance(event, dict) or not isinstance(event.get("id"), str) or not isinstance(event.get("type"), str):
            return {"ok": False, "error": "malformed"}
        state = self._report_state()
        if event["id"] in state["seen"]:
            return {"ok": True, "duplicate": True}
        state["seen"] = (state["seen"] + [event["id"]])[-SEEN_EVENTS_KEPT:]
        state["lastEventAt"], state["lastEventType"] = self.clock(), event["type"]
        kind, drive_id = event["type"], event.get("driveId")
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        if kind == "apply_result":
            self._apply_result(state, data)
        elif isinstance(drive_id, str):
            drive = self._drive(drive_id)
            pending = state["nightlyDrives"]
            # Older completed drives have endedAt and lastReportAt, but no per-drive receipt.
            reported = drive.get("reportedAt") is not None or drive_id in (state.get("nightlyReceipt") or {}).get("drives", [])
            if ("receivedIds" not in drive and drive_id not in pending and drive.get("endedAt") is not None and
                    state.get("lastReportAt") is not None and drive["endedAt"] <= state["lastReportAt"]):
                reported = True
            received = drive.setdefault("receivedIds", [])
            if event["id"] not in received:
                if kind == "drive_start":
                    drive["start"] = data
                    drive.setdefault("startedAt", self.clock())
                    if not reported and not drive.get("ended"):
                        state["lastDriveId"] = drive_id
                elif kind == "drive_ready":
                    drive["ready"] = data
                elif kind == "summary":
                    drive.setdefault("summaries", []).append(data)
                elif kind == "drive_end":
                    drive.update({"end": data, "ended": True, "restore": data.get("restore")})
                    drive.setdefault("endedAt", self.clock())
                else:
                    drive.setdefault("events", []).append({"type": kind, "data": data})
                if reported:
                    drive.setdefault("reportedAt", state.get("lastReportAt"))
                drive["lastRecordAt"] = self.clock()
                received.append(event["id"])
                self._save_drive(drive)
            if not reported:
                if drive_id not in pending:
                    pending.append(drive_id)
                state["nightlyDue"] = max(state.get("nightlyDue") or 0, drive["lastRecordAt"] + NIGHTLY_DELAY_S)
        self.store.put_json("state.json", state)
        return {"ok": True}

    def _create_suggestion(self, *, mode: str, change: dict, reason: str, drive_id: str | None) -> dict:
        if not SUGGESTIONS_BUILT_IN:
            raise RuntimeError("suggestions are not built in")
        if mode not in ("live", "nightly") or reason == "owner_request":
            raise ValueError("reserved owner request")
        return self._create_request(mode=mode, change=change, reason=reason, drive_id=drive_id)

    @staticmethod
    def _owner_binding(record: dict) -> str:
        # The one-time link grants the exact values shown in the notification.
        fields = {k: record[k] for k in ("id", "mode", "change", "reason", "created", "ownerNonce")}
        payload = json.dumps(fields, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        return hashlib.sha256(payload).hexdigest()

    def request_owner_change(self, change: dict) -> tuple[str, bool]:
        """Private operator entry point. No HTTP route or incoming event calls this."""
        validate_owner_change(change)
        target = urlsplit(self.public_url)
        if (target.scheme != "https" or not target.hostname or target.username is not None or
                target.password is not None or target.query or target.fragment):
            raise GuardError("The approval endpoint needs an HTTPS PUBLIC_URL without credentials, query or fragment.")
        record = self._create_request(mode="owner", change=change, reason="owner_request", drive_id=None)
        message = (self._describe(record) + "\nApprove within 12 hours. The agent checks that the truck "
                   "is parked before applying these settings.")
        sent = self.notifier.send("Ram: approve your settings request", message, priority=3,
                                  actions=self._actions(record))
        return record["id"], sent

    def _create_request(self, *, mode: str, change: dict, reason: str, drive_id: str | None) -> dict:
        record = {"id": secrets.token_hex(8), "mode": mode, "change": change, "reason": reason,
                  "driveId": drive_id, "created": self.clock(), "status": "pending", "approval": None,
                  "approveToken": secrets.token_urlsafe(24)}
        if mode == "owner":
            # Retries and overlapping service instances cannot mint a second
            # device transaction for the same phone button.
            record["ownerNonce"] = secrets.token_hex(16)
        else:
            record["declineToken"] = secrets.token_urlsafe(24)
        self._save_suggestion(record)
        actions = ("approve",) if mode == "owner" else ("approve", "decline")
        for action in actions:
            token = record[action + "Token"]
            entry = {"sid": record["id"], "action": action}
            if mode == "owner":
                entry["ownerBinding"] = self._owner_binding(record)
            self.store.put_json(f"tokens/{token}.json", entry)
        return record

    def _actions(self, record: dict) -> list:
        actions = [("Approve", f"{self.public_url}/a/{record['approveToken']}")]
        if "declineToken" in record:
            actions.append(("Decline", f"{self.public_url}/d/{record['declineToken']}"))
        return actions

    def _apply_result(self, state: dict, data: dict) -> None:
        sid, status = data.get("suggestionId"), str(data.get("status"))
        suggestion = self._suggestion(sid) if isinstance(sid, str) else None
        if suggestion is None:
            return
        if suggestion["status"] != "approved":
            return
        if suggestion["mode"] == "owner":
            if status not in OWNER_RECEIPT_STATUSES:
                return
        elif status != "applied" and not status.startswith("refused:"):
            return
        suggestion["status"] = status
        self._save_suggestion(suggestion)
        if suggestion["mode"] == "owner":
            self.store.delete(f"owner-delivery/{sid}.json")
        else:
            state["approved"] = [s for s in state["approved"] if s != sid]
        if status == "applied":
            if suggestion["mode"] == "live" and suggestion["driveId"]:
                drive = self._drive(suggestion["driveId"])
                drive["trial"] = {"suggestionId": sid, "reason": suggestion["reason"], "change": suggestion["change"]}
                self._save_drive(drive)
            description = self._describe(suggestion)
            if suggestion["mode"] == "owner":
                self.notifier.send("Ram: requested settings saved", description +
                                   "\nThe truck saved these values. Some settings take effect after a restart.")
            else:
                self.notifier.send("Ram advisor: Applied", description + " is now active.")
        elif suggestion["mode"] == "owner":
            detail = ("Some values may have changed; the agent could not confirm a complete rollback."
                      if status == "refused:partial_owner_change" else "The agent did not confirm this request as applied.")
            self.notifier.send("Ram: settings request outcome", f"{detail}\n{status}\n{self._describe(suggestion)}")
        else:
            self.notifier.send("Ram advisor: Not applied", f"{self._describe(suggestion)} was not applied ({status}).")

    @staticmethod
    def _describe(suggestion: dict) -> str:
        change = suggestion["change"]
        if suggestion["mode"] == "owner":
            return "\n".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in sorted(change.items()))
        if "NeuralNetworkLateralControl" in change:
            return "Neural-network steering " + ("on" if change["NeuralNetworkLateralControl"] else "off")
        if "LagdToggleDelay" in change:
            return f"Fixed steering delay setting {change['LagdToggleDelay']:.2f} s"
        return "Live steering-delay learning " + ("on" if change.get("LagdToggle") else "off")

    # --- owner taps ---------------------------------------------------------
    def _token(self, token: str, action: str):
        entry = self.store.get_json(f"tokens/{token}.json") if token.replace("-", "").replace("_", "").isalnum() else None
        if not entry or entry.get("action") != action:
            return None
        record = self._suggestion(entry["sid"])
        if not record or record.get(action + "Token") != token:
            return None
        if "ownerBinding" in entry or record.get("mode") == "owner":
            if record.get("mode") != "owner" or record.get("reason") != "owner_request":
                return None
            try:
                binding = self._owner_binding(record)
            except (KeyError, TypeError, ValueError):
                return None
            if entry.get("ownerBinding") != binding:
                return None
        elif not SUGGESTIONS_BUILT_IN:
            return None
        return record

    def approve(self, token: str):
        suggestion = self._token(token, "approve")
        if suggestion is None:
            return 404, "Unknown approval link."
        if suggestion["status"] != "pending":
            return 409, "This suggestion was already handled."
        now = self.clock()
        owner = suggestion["mode"] == "owner"
        if now - suggestion["created"] > SUGGESTION_WINDOW_S[suggestion["mode"]]:
            if not owner:
                suggestion["status"] = "expired"
                self._save_suggestion(suggestion)
            return 410, "This suggestion expired."
        if owner:
            try:
                validate_owner_change(suggestion["change"])
            except GuardError as error:
                return 400, str(error)
        nonce = suggestion["ownerNonce"] if owner else secrets.token_hex(16)
        suggestion["approval"] = sign_approval(self.key, suggestion_id=suggestion["id"], mode=suggestion["mode"],
                                               change=suggestion["change"], issued_at=now, nonce=nonce)
        if owner:
            sid = suggestion["id"]
            # Never update the shared drive/legacy state here. Index first so
            # an interrupted click is either still pending or durably queued.
            self.store.put_json(f"owner-delivery/{sid}.json", {"id": sid})
            if not self.store.put_if_absent(f"owner-grants/{sid}.json", suggestion["approval"]):
                return 409, "This owner request was already handled."
            return 200, "Approved for 12 hours. The agent checks that the truck is parked before applying."
        suggestion["status"] = "approved"
        state = self._state()
        if suggestion["id"] not in state["approved"]:
            state["approved"].append(suggestion["id"])
            self.store.put_json("state.json", state)
        self._save_suggestion(suggestion)
        if suggestion["mode"] == "live":
            return 200, "Approved. It applies once the truck is in Park, within 10 minutes."
        return 200, "Approved. The Comma applies it while the truck is parked, before your next drive."

    def decline(self, token: str):
        suggestion = self._token(token, "decline") if SUGGESTIONS_BUILT_IN else None
        if suggestion is None:
            return 404, "Unknown link."
        if suggestion["status"] != "pending":
            return 409, "This suggestion was already handled."
        suggestion["status"] = "declined"
        self._save_suggestion(suggestion)
        if suggestion["mode"] == "live" and suggestion["driveId"]:
            drive = self._drive(suggestion["driveId"])
            drive["declinedReasons"] = sorted(set(drive["declinedReasons"]) | {suggestion["reason"]})
            self._save_drive(drive)
        return 200, "Declined."

    # --- Comma polling -----------------------------------------------------------
    def approvals_for_comma(self, *, owner=False) -> list:
        if owner:
            out = []
            for key in self.store.list_keys("owner-delivery/"):
                sid = key.removeprefix("owner-delivery/").removesuffix(".json")
                record = self._suggestion(sid)
                if record and record["mode"] == "owner":
                    if record["status"] == "approved":
                        # Redeliver the exact grant even after expiry. Only the
                        # agent can distinguish an unused grant from a lost ack.
                        out.append(record["approval"])
                    elif record["status"] != "pending":
                        self.store.delete(key)  # finish an interrupted terminal acknowledgement
            # A later approved value for the same key should be applied last.
            # Simultaneous service timestamps use a stable request-ID tie break.
            return sorted(out, key=lambda grant: (grant["issued_at"], grant["suggestion_id"]))
        if not SUGGESTIONS_BUILT_IN:
            return []
        state = self._state()
        now, out, keep = self.clock(), [], []
        for sid in state["approved"]:
            suggestion = self._suggestion(sid)
            if suggestion is None or suggestion["status"] != "approved" or not suggestion.get("approval"):
                continue
            if suggestion["mode"] == "owner":
                continue
            if now - suggestion["approval"]["issued_at"] > APPROVAL_TTL_S[suggestion["mode"]]:
                suggestion["status"] = "approval_expired"
                self._save_suggestion(suggestion)
                continue
            out.append(suggestion["approval"])
            keep.append(sid)
        if keep != state["approved"]:
            state["approved"] = keep
            self.store.put_json("state.json", state)
        return out

    # --- report ---------------------------------------------------------------------
    def _archive_report(self, state: dict) -> None:
        receipt = state.get("nightlyReceipt")
        if receipt is None:
            return
        for drive_id in receipt["drives"]:
            drive = self._drive(drive_id)
            if drive.get("reportedAt") is None:
                drive["reportedAt"] = receipt["at"]
                self._save_drive(drive)
        self.store.put_json(f"reports/{int(receipt['at'])}.json", {"drives": receipt["drives"]})
        del state["nightlyReceipt"]
        self.store.put_json("state.json", state)

    def run_nightly(self) -> bool:
        """Send the report when it is due. It carries no button and no advice.

        The body comes from report_body.report_lines over every drive since the last report: time, blind-spot
        availability, steering-limit warnings, steering turned off while moving and why, lane changes, hard
        braking, recording completeness, and the closing line.
        """
        state = self._report_state()
        self._archive_report(state)
        due = state.get("nightlyDue")
        if self._confirmed_report is None:
            if due is None or self.clock() < due or not state["nightlyDrives"]:
                return False
            ids = list(state["nightlyDrives"])
            lines = report_lines([self._drive(d) for d in ids], REPORT_CLOSING_LINE)
            try:
                sent = self.notifier.send(TITLE_REPORT, "\n".join(lines), priority=3, actions=None)
            except Exception:
                sent = False
            if not sent:
                return False
            self._confirmed_report = {"drives": ids, "at": self.clock()}
        receipt = self._confirmed_report
        state["nightlyDrives"] = [d for d in state["nightlyDrives"] if d not in receipt["drives"]]
        if not state["nightlyDrives"]:
            state["nightlyDue"] = None
        # Record delivery and dequeue in one write before any optional archive writes.
        state.update({"nightlyReceipt": receipt, "lastReportAt": receipt["at"]})
        self.store.put_json("state.json", state)
        self._confirmed_report = None
        self._archive_report(state)
        return True


def dispatch(service: AdvisorService, method: str, path: str, headers, body: bytes):
    """Route one request. Returns (status, content_type, body_bytes)."""
    def text(status, message):
        return status, "text/plain; charset=utf-8", message.encode("utf-8")

    def as_json(status, value):
        return status, "application/json", json.dumps(value, separators=(",", ":")).encode("utf-8")

    if method == "GET" and path == "/healthz":
        return text(200, "ok")
    if path in ("/v1/events", "/v1/approvals", "/v1/owner/approvals", "/v1/status"):
        try:
            verify_request(service.key, method, path, body, headers)
        except WireError:
            return text(401, "unauthorized")
        if method == "POST" and path == "/v1/events":
            try:
                event = json.loads(body)
            except ValueError:
                return text(400, "bad json")
            return as_json(200, service.handle_event(event))
        if method == "GET" and path in ("/v1/approvals", "/v1/owner/approvals"):
            return as_json(200, {"approvals": service.approvals_for_comma(owner=path == "/v1/owner/approvals")})
        if method == "GET" and path == "/v1/status":
            state = service._state()
            last_drive = None
            if state.get("lastDriveId"):
                drive = service._drive(state["lastDriveId"])
                last = drive["summaries"][-1] if drive["summaries"] else {}
                newest = drive["events"][-1] if drive["events"] else None
                last_drive = {"summaries": len(drive["summaries"]), "ended": drive["ended"],
                              "restore": drive.get("restore"),
                              "lastEvent": (newest["type"] + ":" + str(newest["data"].get("reason"))) if newest else None,
                              "last": {k: last.get(k) for k in STATUS_FIELDS} if last else None}
            pending = len(state["approved"]) + len(service.approvals_for_comma(owner=True))
            return as_json(200, {"lastEventAt": state.get("lastEventAt"), "lastEventType": state.get("lastEventType"),
                                 "pendingApprovals": pending, "nightlyDue": state.get("nightlyDue"),
                                 "lastDrive": last_drive})
        return text(405, "method not allowed")
    if method == "POST" and path.startswith("/a/"):
        return text(*service.approve(path[3:]))
    if method == "POST" and path.startswith("/d/"):
        return text(*service.decline(path[3:]))
    return text(404, "not found")


def service_from_env() -> AdvisorService:  # pragma: no cover - private Railway project environment
    import boto3
    from botocore.config import Config

    from service_notify import Notifier
    from service_store import S3Store

    env = os.environ
    client = boto3.client("s3", endpoint_url=env["S3_ENDPOINT"], aws_access_key_id=env["S3_ACCESS_KEY_ID"],
                          aws_secret_access_key=env["S3_SECRET_ACCESS_KEY"], region_name=env.get("S3_REGION") or "auto",
                          config=Config(s3={"addressing_style": "virtual"}))
    return AdvisorService(S3Store(client, env["S3_BUCKET"]), Notifier(env["NTFY_TOPIC"]),
                          bytes.fromhex(env["ADVISOR_KEY"]), env["PUBLIC_URL"])


def serve() -> None:  # pragma: no cover - runs on Railway
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    service = service_from_env()
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def _handle(self, method):
            length = int(self.headers.get("Content-Length") or 0)
            if length > 262_144:
                self.send_response(413)
                self.end_headers()
                return
            body = self.rfile.read(length) if length else b""
            with lock:
                status, content_type, out = dispatch(service, method, self.path.split("?", 1)[0], self.headers, body)
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

        def log_message(self, *args):
            pass   # paths carry one-time tokens; keep them out of logs

    def nightly_loop():
        while True:
            time.sleep(60)
            try:
                with lock:
                    service.run_nightly()
            except Exception as error:
                print(f"nightly_error {type(error).__name__}", flush=True)

    threading.Thread(target=nightly_loop, name="nightly", daemon=True).start()
    print("advisor_service_started", flush=True)
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), Handler).serve_forever()


if __name__ == "__main__":
    serve()
