#!/usr/bin/env python3
"""Drive Advisor Railway service.

Receives signed events from the Comma agent, stores what each drive sends, and
sends a report about an hour after the last drive. It measures and reports; it
suggests nothing. No rule in it turns a measured number into advice.

What is left of the suggestion path (records, Approve/Decline links, signed
approvals for the Comma) is kept for a future build and is locked on this one:
see the lock constant below. Nothing in this service creates a suggestion.
"""
from __future__ import annotations

import json
import os
import secrets
import time

from approval import APPROVAL_TTL_S, sign_approval
from report_body import report_lines
from wire import WireError, verify_request

NIGHTLY_DELAY_S = 3600
SUGGESTION_WINDOW_S = {"live": 30 * 60, "nightly": 12 * 3600}
SEEN_EVENTS_KEPT = 2000
TITLE_REPORT = "Ram drive report"
REPORT_CLOSING_LINE = "No changes are suggested. This report only measures."
STATUS_FIELDS = ("w", "n", "windowS", "steeringS", "movingS", "warnEvents")     # of a drive's newest summary

# Lock 1 of the suggestion path. A code constant: no constructor argument,
# environment variable, file or request can open it. While it is False the
# service creates no suggestion, answers "not found" on the approve and decline
# links and hands no approval to the Comma.
SUGGESTIONS_BUILT_IN = False


class AdvisorService:
    def __init__(self, store, notifier, key: bytes, public_url: str, *, clock=time.time):
        self.store = store
        self.notifier = notifier
        self.key = key
        self.public_url = public_url.rstrip("/")
        self.clock = clock

    # --- records -----------------------------------------------------------
    def _state(self) -> dict:
        return self.store.get_json("state.json") or {"seen": [], "approved": [], "nightlyDue": None,
                                                     "nightlyDrives": []}

    def _drive(self, drive_id: str) -> dict:
        """One drive: what drive_start, drive_ready and drive_end said, every summary and every other record."""
        return self.store.get_json(f"drives/{drive_id}.json") or {
            "driveId": drive_id, "start": None, "ready": None, "summaries": [], "events": [], "end": None,
            "ended": False, "restore": None, "trial": None, "declinedReasons": []}

    def _save_drive(self, drive: dict) -> None:
        self.store.put_json(f"drives/{drive['driveId']}.json", drive)

    def _suggestion(self, sid: str) -> dict | None:
        return self.store.get_json(f"suggestions/{sid}.json")

    def _save_suggestion(self, suggestion: dict) -> None:
        self.store.put_json(f"suggestions/{suggestion['id']}.json", suggestion)

    # --- events from the Comma ---------------------------------------------
    def handle_event(self, event: dict) -> dict:
        if not isinstance(event, dict) or not isinstance(event.get("id"), str) or not isinstance(event.get("type"), str):
            return {"ok": False, "error": "malformed"}
        state = self._state()
        if event["id"] in state["seen"]:
            return {"ok": True, "duplicate": True}
        state["seen"] = (state["seen"] + [event["id"]])[-SEEN_EVENTS_KEPT:]
        state["lastEventAt"], state["lastEventType"] = self.clock(), event["type"]
        kind, drive_id = event["type"], event.get("driveId")
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        if kind == "drive_start" and isinstance(drive_id, str):
            drive = self._drive(drive_id)
            drive.update({"start": data, "startedAt": self.clock()})
            self._save_drive(drive)
            state["nightlyDue"] = None   # a new drive postpones the report
            state["lastDriveId"] = drive_id
        elif kind == "drive_ready" and isinstance(drive_id, str):
            drive = self._drive(drive_id)
            drive["ready"] = data
            self._save_drive(drive)
        elif kind == "summary" and isinstance(drive_id, str):
            drive = self._drive(drive_id)
            drive["summaries"].append(data)
            self._save_drive(drive)
        elif kind == "drive_end" and isinstance(drive_id, str):
            drive = self._drive(drive_id)
            drive.update({"end": data, "ended": True, "endedAt": self.clock(), "restore": data.get("restore")})
            self._save_drive(drive)
            if drive_id not in state["nightlyDrives"]:
                state["nightlyDrives"].append(drive_id)
            state["nightlyDue"] = self.clock() + NIGHTLY_DELAY_S
        elif kind == "apply_result":
            self._apply_result(state, data)
        elif isinstance(drive_id, str):
            # Every other record of the drive (braking, lane change, limit event, settings change, listening
            # disabled) is kept, however long the drive is.
            drive = self._drive(drive_id)
            drive["events"].append({"type": kind, "data": data})
            self._save_drive(drive)
        self.store.put_json("state.json", state)
        return {"ok": True}

    def _create_suggestion(self, *, mode: str, change: dict, reason: str, drive_id: str | None) -> dict:
        if not SUGGESTIONS_BUILT_IN:
            raise RuntimeError("suggestions are not built in")
        record = {"id": secrets.token_hex(8), "mode": mode, "change": change, "reason": reason,
                  "driveId": drive_id, "created": self.clock(), "status": "pending", "approval": None,
                  "approveToken": secrets.token_urlsafe(24), "declineToken": secrets.token_urlsafe(24)}
        self._save_suggestion(record)
        for action in ("approve", "decline"):
            self.store.put_json(f"tokens/{record[action + 'Token']}.json", {"sid": record["id"], "action": action})
        return record

    def _actions(self, record: dict) -> list:
        return [("Approve", f"{self.public_url}/a/{record['approveToken']}"),
                ("Decline", f"{self.public_url}/d/{record['declineToken']}")]

    def _apply_result(self, state: dict, data: dict) -> None:
        sid, status = data.get("suggestionId"), str(data.get("status"))
        suggestion = self._suggestion(sid) if isinstance(sid, str) else None
        if suggestion is None:
            return
        suggestion["status"] = status
        self._save_suggestion(suggestion)
        state["approved"] = [s for s in state["approved"] if s != sid]
        if status == "applied":
            if suggestion["mode"] == "live" and suggestion["driveId"]:
                drive = self._drive(suggestion["driveId"])
                drive["trial"] = {"suggestionId": sid, "reason": suggestion["reason"], "change": suggestion["change"]}
                self._save_drive(drive)
            self.notifier.send("Ram advisor: Applied", self._describe(suggestion) + " is now active.")
        else:
            self.notifier.send("Ram advisor: Not applied", f"{self._describe(suggestion)} was not applied ({status}).")

    @staticmethod
    def _describe(suggestion: dict) -> str:
        change = suggestion["change"]
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
        return self._suggestion(entry["sid"])

    def approve(self, token: str):
        suggestion = self._token(token, "approve") if SUGGESTIONS_BUILT_IN else None
        if suggestion is None:
            return 404, "Unknown approval link."
        if suggestion["status"] != "pending":
            return 409, "This suggestion was already handled."
        now = self.clock()
        if now - suggestion["created"] > SUGGESTION_WINDOW_S[suggestion["mode"]]:
            suggestion["status"] = "expired"
            self._save_suggestion(suggestion)
            return 410, "This suggestion expired."
        suggestion["approval"] = sign_approval(self.key, suggestion_id=suggestion["id"], mode=suggestion["mode"],
                                               change=suggestion["change"], issued_at=now,
                                               nonce=secrets.token_hex(16))
        suggestion["status"] = "approved"
        self._save_suggestion(suggestion)
        state = self._state()
        state["approved"] = state["approved"] + [suggestion["id"]]
        self.store.put_json("state.json", state)
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
    def approvals_for_comma(self) -> list:
        if not SUGGESTIONS_BUILT_IN:
            return []
        state = self._state()
        now, out, keep = self.clock(), [], []
        for sid in state["approved"]:
            suggestion = self._suggestion(sid)
            if suggestion is None or suggestion["status"] != "approved" or not suggestion.get("approval"):
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
    def run_nightly(self) -> bool:
        """Send the report when it is due. It carries no button and no advice.

        The body comes from report_body.report_lines over every drive since the last report: time, blind-spot
        availability, steering-limit warnings, steering turned off while moving and why, lane changes, hard
        braking, recording completeness, and the closing line.
        """
        state = self._state()
        due = state.get("nightlyDue")
        if due is None or self.clock() < due or not state["nightlyDrives"]:
            return False
        lines = report_lines([self._drive(d) for d in state["nightlyDrives"]], REPORT_CLOSING_LINE)
        # Send first. The pending report is cleared only after the notifier confirms delivery, so a failed
        # send is retried on the next run instead of being lost.
        try:
            sent = bool(self.notifier.send(TITLE_REPORT, "\n".join(lines), priority=3, actions=None))
        except Exception:
            sent = False
        if not sent:
            return False
        self.store.put_json(f"reports/{int(self.clock())}.json", {"drives": list(state["nightlyDrives"])})
        state.update({"nightlyDue": None, "nightlyDrives": [], "lastReportAt": self.clock()})
        self.store.put_json("state.json", state)
        return True


def dispatch(service: AdvisorService, method: str, path: str, headers, body: bytes):
    """Route one request. Returns (status, content_type, body_bytes)."""
    def text(status, message):
        return status, "text/plain; charset=utf-8", message.encode("utf-8")

    def as_json(status, value):
        return status, "application/json", json.dumps(value, separators=(",", ":")).encode("utf-8")

    if method == "GET" and path == "/healthz":
        return text(200, "ok")
    if path in ("/v1/events", "/v1/approvals", "/v1/status"):
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
        if method == "GET" and path == "/v1/approvals":
            return as_json(200, {"approvals": service.approvals_for_comma()})
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
            return as_json(200, {"lastEventAt": state.get("lastEventAt"), "lastEventType": state.get("lastEventType"),
                                 "pendingApprovals": len(state["approved"]), "nightlyDue": state.get("nightlyDue"),
                                 "lastDrive": last_drive})
        return text(405, "method not allowed")
    if method == "POST" and path.startswith("/a/"):
        return text(*service.approve(path[3:]))
    if method == "POST" and path.startswith("/d/"):
        return text(*service.decline(path[3:]))
    return text(404, "not found")


def serve() -> None:  # pragma: no cover - runs on Railway
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    import boto3
    from botocore.config import Config

    from service_notify import Notifier
    from service_store import S3Store

    env = os.environ
    client = boto3.client("s3", endpoint_url=env["S3_ENDPOINT"], aws_access_key_id=env["S3_ACCESS_KEY_ID"],
                          aws_secret_access_key=env["S3_SECRET_ACCESS_KEY"], region_name=env.get("S3_REGION") or "auto",
                          config=Config(s3={"addressing_style": "virtual"}))
    service = AdvisorService(S3Store(client, env["S3_BUCKET"]), Notifier(env["NTFY_TOPIC"]),
                             bytes.fromhex(env["ADVISOR_KEY"]), env["PUBLIC_URL"])
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
    ThreadingHTTPServer(("0.0.0.0", int(env.get("PORT", "8080"))), Handler).serve_forever()


if __name__ == "__main__":
    serve()
