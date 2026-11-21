"""Phone notifications through ntfy (JSON publish API), with Approve/Decline
buttons that call the service. The topic is secret and lives only in Railway."""
from __future__ import annotations

import json
import urllib.request

LIVE_PRIORITY = 4      # high: plays the notification sound
REPORT_PRIORITY = 3    # default


def _post(url: str, body: bytes, timeout: float = 10.0) -> int:
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status


class Notifier:
    def __init__(self, topic: str, *, server: str = "https://ntfy.sh", post=_post):
        self.topic = topic
        self.server = server.rstrip("/")
        self.post = post

    def send(self, title: str, message: str, *, priority: int = REPORT_PRIORITY, actions=None) -> bool:
        payload = {"topic": self.topic, "title": title, "message": message, "priority": priority}
        if actions:
            payload["actions"] = [{"action": "http", "label": label, "url": url, "method": "POST", "clear": True}
                                  for label, url in actions]
        try:
            status = self.post(self.server, json.dumps(payload).encode("utf-8"))
        except Exception:
            return False
        return 200 <= status < 300
