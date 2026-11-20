"""Uplink sends signed events in order, retries failures, and collects approvals."""
import json
import unittest

from uplink import Uplink
from wire import verify_request

KEY = b"k" * 32


class FakeServer:
    def __init__(self):
        self.events = []
        self.fail = False
        self.approvals = []
        self.paths = []

    def __call__(self, method, url, headers, body, timeout):
        path = url.split("example.test", 1)[1]
        verify_request(KEY, method, path, body, headers)
        self.paths.append(path)
        if self.fail:
            raise OSError("offline")
        if method == "POST":
            self.events.append(json.loads(body))
            return 200, b'{"ok":true}'
        return 200, json.dumps({"approvals": self.approvals}).encode()


class UplinkTests(unittest.TestCase):
    def setUp(self):
        self.server = FakeServer()
        self.uplink = Uplink("https://example.test/", KEY, opener=self.server)

    def test_events_sent_in_order_and_cleared(self):
        for i in range(3):
            self.uplink.send({"id": str(i)})
        self.uplink.run_once()
        self.assertEqual([e["id"] for e in self.server.events], ["0", "1", "2"])
        self.uplink.run_once()
        self.assertEqual(len(self.server.events), 3)

    def test_failure_keeps_events_for_next_round(self):
        self.uplink.send({"id": "a"})
        self.server.fail = True
        self.uplink.run_once()
        self.server.fail = False
        self.uplink.run_once()
        self.assertEqual([e["id"] for e in self.server.events], ["a"])

    def test_queue_drops_oldest_when_full(self):
        small = Uplink("https://example.test", KEY, opener=self.server, max_queue=2)
        for i in range(3):
            small.send({"id": str(i)})
        small.run_once()
        self.assertEqual([e["id"] for e in self.server.events], ["1", "2"])

    def test_approvals_collected_once(self):
        self.server.approvals = [{"nonce": "n" * 16}]
        self.uplink.run_once()
        self.assertEqual(self.uplink.approvals(), [{"nonce": "n" * 16}])
        self.assertEqual(self.uplink.approvals(), [])

    def test_requests_use_expected_paths(self):
        self.uplink.send({"id": "a"})
        self.uplink.run_once()
        self.assertEqual(self.server.paths, ["/v1/events", "/v1/approvals"])


if __name__ == "__main__":
    unittest.main()
