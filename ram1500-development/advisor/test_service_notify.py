"""ntfy payloads: topic, priority and Approve/Decline buttons."""
import json
import unittest

from service_notify import Notifier


class NotifierTests(unittest.TestCase):
    def test_payload_with_buttons(self):
        sent = []
        notifier = Notifier("topic-x", post=lambda url, body: sent.append((url, json.loads(body))) or 200)
        self.assertTrue(notifier.send("T", "M", priority=4, actions=[("Approve", "https://s/a/1")]))
        url, payload = sent[0]
        self.assertEqual(url, "https://ntfy.sh")
        self.assertEqual((payload["topic"], payload["priority"]), ("topic-x", 4))
        self.assertEqual(payload["actions"], [{"action": "http", "label": "Approve", "url": "https://s/a/1",
                                               "method": "POST", "clear": True}])

    def test_failure_returns_false(self):
        def broken(url, body):
            raise OSError("offline")
        self.assertFalse(Notifier("t", post=broken).send("T", "M"))


if __name__ == "__main__":
    unittest.main()
