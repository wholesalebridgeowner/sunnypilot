"""Background sender for the Comma agent.

Events wait in a bounded queue (oldest dropped first) and go out oldest-first
over signed HTTPS. A failed send stops the round and is retried next round.
Approvals are fetched each round. Nothing here blocks the agent loop.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from collections import deque

from wire import sign_request

MAX_RESPONSE_BYTES = 1_000_000


def _http(method: str, url: str, headers: dict, body: bytes, timeout: float):
    request = urllib.request.Request(url, data=body if method == "POST" else None, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.read(MAX_RESPONSE_BYTES)


class Uplink:
    def __init__(self, base_url: str, key: bytes, *, opener=None, max_queue: int = 2000,
                 interval_s: float = 5.0, timeout_s: float = 10.0):
        self.base = base_url.rstrip("/")
        self.key = key
        self.opener = opener or _http
        self.queue = deque(maxlen=max_queue)
        self.inbox = []
        self.lock = threading.Lock()
        self.interval_s = interval_s
        self.timeout_s = timeout_s

    def send(self, event: dict) -> None:
        with self.lock:
            self.queue.append(event)

    def approvals(self) -> list:
        with self.lock:
            out, self.inbox = self.inbox, []
        return out

    def _request(self, method: str, path: str, body: bytes):
        headers = sign_request(self.key, method, path, body)
        headers["Content-Type"] = "application/json"
        try:
            status, payload = self.opener(method, self.base + path, headers, body, self.timeout_s)
        except Exception:
            return None
        return payload if 200 <= status < 300 else None

    def run_once(self) -> None:
        while True:
            with self.lock:
                if not self.queue:
                    break
                event = self.queue[0]
            body = json.dumps(event, separators=(",", ":"), sort_keys=True).encode("utf-8")
            if self._request("POST", "/v1/events", body) is None:
                return   # offline: keep the queue, try again next round
            with self.lock:
                if self.queue and self.queue[0] is event:
                    self.queue.popleft()
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
