"""Background sender for the Comma agent.

Events wait in a bounded queue (oldest dropped first, and counted) and go out
oldest-first over signed HTTPS. A failed send stops the round and is retried
next round. Nothing here blocks the agent loop.

While the truck is onroad at most 2 events a second are sent. The agent's
self-limit measures the whole process, and a backlog sent after a stretch
without network would otherwise cost enough to stop the listening for that
drive. A normal minute's two or three events are never held back. Offroad
there is no limit.

The agent requests owner approvals while offroad or parked. Advisor-suggested
approvals use their own build gate and queue.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from collections import deque

import applier as apply_lock
from messages import encode
from wire import sign_request

MAX_RESPONSE_BYTES = 1_000_000
ONROAD_POST_SPACING_S = 0.5     # while onroad: at most 2 events per second


def _http(method: str, url: str, headers: dict, body: bytes, timeout: float):
    request = urllib.request.Request(url, data=body if method == "POST" else None, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.read(MAX_RESPONSE_BYTES)


class Uplink:
    def __init__(self, base_url: str, key: bytes, *, opener=None, max_queue: int = 2000,
                 interval_s: float = 5.0, timeout_s: float = 10.0, monotonic=time.monotonic, sleep=time.sleep):
        self.base = base_url.rstrip("/")
        self.key = key
        self.opener = opener or _http
        self.queue = deque(maxlen=max_queue)
        self.inbox = []
        self.owner_inbox = {}
        self.owner_poll = False
        self.lock = threading.Lock()
        self.interval_s = interval_s
        self.timeout_s = timeout_s
        self.monotonic = monotonic
        self.sleep = sleep
        self.onroad = False
        self._dropped = 0               # events the full queue discarded since take_dropped was last called
        self._last_post = None          # when the newest post began

    # --- called by the agent ------------------------------------------------
    def send(self, event: dict) -> None:
        with self.lock:
            if len(self.queue) == self.queue.maxlen:
                self._dropped += 1      # the append below pushes the oldest event out
            self.queue.append(event)

    def take_dropped(self) -> int:
        """How many events the full queue has discarded since this was last asked."""
        with self.lock:
            dropped, self._dropped = self._dropped, 0
        return dropped

    def queue_len(self) -> int:
        with self.lock:
            return len(self.queue)

    def set_onroad(self, onroad: bool) -> None:
        """The agent says when a drive starts and ends; sending is paced in between."""
        self.onroad = bool(onroad)

    def approvals(self) -> list:
        with self.lock:
            out, self.inbox = self.inbox, []
        return out

    def set_owner_poll(self, ready: bool) -> None:
        with self.lock:
            self.owner_poll = ready

    def owner_approvals(self) -> list:
        with self.lock:
            out = list(self.owner_inbox.values())
            self.owner_inbox.clear()
        return out

    # --- the sender ---------------------------------------------------------
    def _request(self, method: str, path: str, body: bytes):
        headers = sign_request(self.key, method, path, body)
        headers["Content-Type"] = "application/json"
        try:
            status, payload = self.opener(method, self.base + path, headers, body, self.timeout_s)
        except Exception:
            return None
        return payload if 200 <= status < 300 else None

    def _wait_for_pace(self) -> None:
        if self.onroad and self._last_post is not None:
            wait = self._last_post + ONROAD_POST_SPACING_S - self.monotonic()
            if wait > 0:
                self.sleep(wait)
        self._last_post = self.monotonic()

    def run_once(self) -> None:
        while True:
            with self.lock:
                if not self.queue:
                    break
                event = self.queue[0]
            self._wait_for_pace()
            if self._request("POST", "/v1/events", encode(event)) is None:
                return   # offline: keep the queue, try again next round
            with self.lock:
                if self.queue and self.queue[0] is event:
                    self.queue.popleft()
        with self.lock:
            owner_poll = self.owner_poll
        if owner_poll:
            payload = self._request("GET", "/v1/owner/approvals", b"")
            if payload is not None:
                try:
                    items = json.loads(payload).get("approvals", [])
                except (ValueError, AttributeError):
                    items = []
                if isinstance(items, list):
                    with self.lock:
                        for item in items:
                            if isinstance(item, dict) and item.get("mode") == "owner" and isinstance(item.get("nonce"), str):
                                self.owner_inbox[item["nonce"]] = item
        if not apply_lock.APPLY_ENABLED:
            return
        payload = self._request("GET", "/v1/approvals", b"")
        if payload is None:
            return
        try:
            approvals = json.loads(payload).get("approvals", [])
        except (ValueError, AttributeError):
            return
        if isinstance(approvals, list):
            with self.lock:
                self.inbox.extend(a for a in approvals if isinstance(a, dict))

    def start(self) -> threading.Thread:
        def loop():
            while True:
                self.run_once()
                time.sleep(self.interval_s)
        thread = threading.Thread(target=loop, name="advisor-uplink", daemon=True)
        thread.start()
        return thread
