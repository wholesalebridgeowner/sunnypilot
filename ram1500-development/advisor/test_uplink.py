"""Uplink sends signed events in order and retries failures, counts what its full queue discards, and is
paced while the truck is onroad. It asks for no approvals: that request belongs to the apply path
(test_dormant_path.py)."""
import json
import unittest

import uplink as uplink_module
from uplink import Uplink
from wire import verify_request

KEY = b"k" * 32


class FakeServer:
    def __init__(self):
        self.events = []
        self.fail = False
        self.approvals = []
        self.paths = []
        self.methods = []
        self.headers = []
        self.bodies = []

    def __call__(self, method, url, headers, body, timeout):
        path = url.split("example.test", 1)[1]
        verify_request(KEY, method, path, body, headers)
        self.paths.append(path)
        self.methods.append(method)
        self.headers.append(dict(headers))
        self.bodies.append(body)
        if self.fail:
            raise OSError("offline")
        if method == "POST":
            self.events.append(json.loads(body))
            return 200, b'{"ok":true}'
        return 200, json.dumps({"approvals": self.approvals}).encode()


class UplinkFixture(unittest.TestCase):
    def setUp(self):
        self.server = FakeServer()
        self.uplink = Uplink("https://example.test/", KEY, opener=self.server)


class UplinkTests(UplinkFixture):
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

    def test_requests_use_expected_paths(self):
        self.uplink.send({"id": "a"})
        self.uplink.run_once()
        self.assertEqual(self.server.paths, ["/v1/events"])

    def test_no_approvals_are_asked_for(self):
        self.server.approvals = [{"nonce": "n" * 16}]            # waiting on the service
        for i in range(3):
            self.uplink.send({"id": str(i)})
            self.uplink.run_once()
        self.uplink.run_once()                                       # a round with nothing to send
        self.assertEqual(self.server.methods, ["POST"] * 3)          # not one GET
        self.assertEqual(self.uplink.approvals(), [])

    def test_only_three_headers_are_handed_to_the_http_library(self):
        self.uplink.send({"id": "a"})
        self.uplink.run_once()
        self.assertEqual(set(self.server.headers[0]), {"Content-Type", "X-Advisor-Ts", "X-Advisor-Sig"})
        self.assertEqual(self.server.headers[0]["Content-Type"], "application/json")

    def test_the_body_is_compact_json_with_sorted_keys(self):
        self.uplink.send({"v": 2, "type": "summary", "id": "a", "driveId": None, "data": {"w": 0, "n": 1}})
        self.uplink.run_once()
        self.assertEqual(self.server.bodies[0], b'{"data":{"n":1,"w":0},"driveId":null,"id":"a","type":"summary","v":2}')


class DropCounterTests(UplinkFixture):
    """The bounded queue discards its oldest event when full; what it discards is counted."""

    def test_queue_of_three_with_five_events_sent_while_offline(self):
        small = Uplink("https://example.test", KEY, opener=self.server, max_queue=3)
        self.server.fail = True
        for i in range(5):
            small.send({"id": str(i)})
            small.run_once()
        self.assertEqual(small.queue_len(), 3)
        self.assertEqual(small.take_dropped(), 2)
        self.assertEqual(small.take_dropped(), 0)                    # counted since the last time it was asked
        self.server.fail = False
        small.run_once()
        self.assertEqual([e["id"] for e in self.server.events], ["2", "3", "4"])
        self.assertEqual(small.queue_len(), 0)

    def test_nothing_is_counted_while_there_is_room(self):
        for i in range(50):
            self.uplink.send({"id": str(i)})
        self.assertEqual((self.uplink.queue_len(), self.uplink.take_dropped()), (50, 0))

    def test_an_event_discarded_while_it_is_being_sent_is_not_sent_twice(self):
        small = Uplink("https://example.test", KEY, opener=self.server, max_queue=1)
        inner = self.server

        def opener(method, url, headers, body, timeout):
            if not inner.events:
                small.send({"id": "late"})                           # arrives while the first post is under way
            return inner(method, url, headers, body, timeout)
        small.opener = opener
        small.send({"id": "first"})
        small.run_once()
        self.assertEqual([e["id"] for e in inner.events], ["first", "late"])
        self.assertEqual(small.take_dropped(), 1)


class FakeTime:
    """A clock that only moves when the uplink sleeps or a request takes time."""

    def __init__(self):
        self.t = 500.0
        self.slept = []

    def monotonic(self):
        return self.t

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.t += seconds


class PacingTests(unittest.TestCase):
    """At most 2 events per second while onroad; no limit offroad."""

    def setUp(self):
        self.time = FakeTime()
        self.posted_at = []
        self.methods = []
        self.latency = 0.0

        def opener(method, url, headers, body, timeout):
            self.methods.append(method)
            self.posted_at.append(self.time.t)
            self.time.t += self.latency
            return 200, b'{"ok":true}'
        self.uplink = Uplink("https://example.test", KEY, opener=opener, monotonic=self.time.monotonic,
                             sleep=self.time.sleep)

    def most_in_one_second(self, times):
        return max(sum(1 for u in times if t <= u < t + 1.0 - 1e-9) for t in times)

    def test_the_spacing(self):
        self.assertEqual(uplink_module.ONROAD_POST_SPACING_S, 0.5)

    def test_a_hundred_queued_events_while_onroad(self):
        self.uplink.set_onroad(True)
        for i in range(100):
            self.uplink.send({"id": str(i)})
        self.uplink.run_once()
        self.assertEqual(len(self.posted_at), 100)
        self.assertEqual(self.most_in_one_second(self.posted_at), 2)
        self.assertAlmostEqual(self.posted_at[-1] - self.posted_at[0], 49.5, places=6)
        self.assertEqual(self.methods, ["POST"] * 100)               # and not one GET

    def test_then_offroad_there_is_no_limit(self):
        self.uplink.set_onroad(True)
        self.uplink.send({"id": "a"})
        self.uplink.run_once()
        self.uplink.set_onroad(False)
        for i in range(100):
            self.uplink.send({"id": str(i)})
        before = self.time.t
        self.uplink.run_once()
        self.assertEqual(len(self.posted_at), 101)
        self.assertEqual(self.time.t, before)                        # no waiting at all
        self.assertEqual(self.most_in_one_second(self.posted_at[1:]), 100)

    def test_the_pace_holds_across_rounds(self):
        self.uplink.set_onroad(True)
        for i in range(6):
            self.uplink.send({"id": str(i)})
            self.uplink.run_once()                                   # one event per round, rounds back to back
        self.assertEqual(self.most_in_one_second(self.posted_at), 2)
        self.assertEqual([round(b - a, 6) for a, b in zip(self.posted_at, self.posted_at[1:])], [0.5] * 5)

    def test_a_normal_minute_is_never_held_back(self):
        # Two or three events a minute, rounds five seconds apart: nothing waits.
        self.uplink.set_onroad(True)
        for i in range(12):
            self.uplink.send({"id": str(i)})
            self.uplink.run_once()
            self.time.t += 5.0
        self.assertEqual(self.time.slept, [])

    def test_a_slow_request_counts_toward_the_spacing(self):
        self.latency = 0.3
        self.uplink.set_onroad(True)
        for i in range(4):
            self.uplink.send({"id": str(i)})
        self.uplink.run_once()
        self.assertEqual([round(s, 6) for s in self.time.slept], [0.2, 0.2, 0.2])
        self.assertEqual(self.most_in_one_second(self.posted_at), 2)

    def test_going_offroad_in_the_middle_of_a_backlog_lifts_the_limit(self):
        self.uplink.set_onroad(True)
        for i in range(20):
            self.uplink.send({"id": str(i)})
        real_sleep = self.time.sleep

        def sleep(seconds):
            real_sleep(seconds)
            if len(self.posted_at) == 5:
                self.uplink.set_onroad(False)                        # the drive ends while the uplink waits
        self.uplink.sleep = sleep
        self.uplink.run_once()
        self.assertEqual(len(self.posted_at), 20)
        self.assertEqual(len(self.time.slept), 5)
        self.assertEqual(len(set(self.posted_at[5:])), 1)            # the rest goes out at once

    def test_an_offline_round_ends_at_the_first_failure_without_waiting(self):
        def offline(method, url, headers, body, timeout):
            self.posted_at.append(self.time.t)
            raise OSError("offline")
        self.uplink.opener = offline
        self.uplink.set_onroad(True)
        for i in range(10):
            self.uplink.send({"id": str(i)})
        self.uplink.run_once()
        self.assertEqual(len(self.posted_at), 1)
        self.assertEqual(self.time.slept, [])
        self.assertEqual(self.uplink.queue_len(), 10)


if __name__ == "__main__":
    unittest.main()
