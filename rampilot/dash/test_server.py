import asyncio
from pathlib import Path
import socket
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from aiohttp import WSServerHandshakeError, WSMsgType
from aiohttp.test_utils import TestClient, TestServer

from rampilot.dash import server


class Source:
  def __init__(self):
    self.count = 0
    self.failed = False

  def snapshot(self):
    if self.failed:
      raise RuntimeError("fixture stopped")
    self.count += 1
    return {"tick": self.count}


class TransportTests(unittest.IsolatedAsyncioTestCase):
  async def asyncSetUp(self):
    self.source = Source()
    self.token = "t" * 43  # public fixture
    self.app = server.create_app(self.source, self.token, probe=lambda: (0, 12))
    self.client = TestClient(TestServer(self.app, host="127.0.0.1"))
    await self.client.start_server()
    self.addAsyncCleanup(self.client.close)
    self.auth = {"Authorization": "Bearer " + self.token}
    self.protocols = [server.PROTOCOL, "rp." + self.token]

  async def test_token_refusal(self):
    for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": "Bearer invalid-µ"}):
      async with self.client.get("/api/state", headers=headers) as response:
        self.assertEqual(response.status, 401)
    async with self.client.get("/api/state?token=dummy", headers=self.auth) as response:
      self.assertEqual(response.status, 400)
    for protocols in ([], [server.PROTOCOL], [server.PROTOCOL, "rp.invalid"]):
      with self.assertRaises(WSServerHandshakeError) as ctx:
        await self.client.ws_connect("/ws", protocols=protocols)
      self.assertEqual(ctx.exception.status, 401)
    self.assertFalse(self.app[server.FEED].clients)

  async def test_origin_and_host(self):
    for headers in ({"Origin": "http://unrelated.invalid"}, {"Host": "unrelated.invalid"}):
      async with self.client.get("/api/state", headers={**self.auth, **headers}) as response:
        self.assertEqual(response.status, 403)
    with self.assertRaises(WSServerHandshakeError) as ctx:
      await self.client.ws_connect("/ws", protocols=self.protocols, origin="http://unrelated.invalid")
    self.assertEqual(ctx.exception.status, 403)

  async def test_same_sample_serves_http_and_socket_and_rejects_writes(self):
    origin = str(self.client.make_url("/")).rstrip("/")
    async with self.client.ws_connect("/ws", protocols=self.protocols, origin=origin) as ws:
      initial = await ws.receive_json(timeout=2)
      self.assertEqual(ws.protocol, server.PROTOCOL)
      async with self.client.get("/api/state", headers=self.auth) as response:
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual((await response.json())["tick"], initial["tick"])
      newer = await ws.receive_json(timeout=2)
      self.assertGreater(newer["tick"], initial["tick"])
      await ws.send_str("ignored")
      self.assertEqual((await ws.receive(timeout=2)).type, WSMsgType.CLOSE)
      self.assertEqual(ws.close_code, 1008)
    async with self.client.post("/api/state", headers=self.auth, json={}) as response:
      self.assertEqual(response.status, 405)

  async def test_source_stops_and_recovers(self):
    async with self.client.ws_connect("/ws", protocols=self.protocols) as ws:
      await ws.receive_json(timeout=2)
      self.source.failed = True
      self.assertEqual((await ws.receive(timeout=2)).type, WSMsgType.CLOSE)
      self.assertEqual(ws.close_code, 1013)
    async with self.client.get("/api/state", headers=self.auth) as response:
      self.assertEqual(response.status, 503)
    self.source.failed = False
    await asyncio.sleep(server.PERIOD + .05)
    async with self.client.get("/api/state", headers=self.auth) as response:
      self.assertEqual(response.status, 200)

  async def test_full_sockets_fall_back_to_polling(self):
    sockets = []
    try:
      for _ in range(server.MAX_CLIENTS):
        sockets.append(await self.client.ws_connect("/ws", protocols=self.protocols))
      with self.assertRaises(WSServerHandshakeError) as ctx:
        await self.client.ws_connect("/ws", protocols=self.protocols)
      self.assertEqual(ctx.exception.status, 503)
      async with self.client.get("/api/state", headers=self.auth) as response:
        self.assertEqual(response.status, 200)
    finally:
      for ws in sockets:
        await ws.close()

  async def test_occupied_port_never_constructs_a_live_source(self):
    with tempfile.TemporaryDirectory() as tmp, socket.socket() as occupied:
      token = Path(tmp) / "token"
      server.init_token(token)
      occupied.bind(("127.0.0.1", 0))
      occupied.listen()
      args = SimpleNamespace(token_file=token, bind="127.0.0.1", port=occupied.getsockname()[1])
      with patch.object(server, "require_companion"), patch.object(server, "LOCK_PATH", Path(tmp) / "lock"), \
           patch("rampilot.dash.shared.SharedSource") as source:
        with self.assertRaises(OSError):
          await server.serve(args)
        source.assert_not_called()

  async def test_expired_cache_and_latched_resource_guard(self):
    feed = self.app[server.FEED]
    feed.updated -= server.CACHE_MAX_AGE + 1
    async with self.client.get("/api/state", headers=self.auth) as response:
      self.assertEqual(response.status, 503)
    feed.stopped.set()
    feed.sample()
    self.assertIsNone(feed.current())

  async def test_real_sampler_stops_on_memory_breach(self):
    samples = iter([(0., 12.), (0., 151.)])
    app = server.create_app(Source(), self.token, probe=lambda: next(samples))
    client = TestClient(TestServer(app, host="127.0.0.1"))
    with patch.object(server, "PERIOD", .01), patch.object(server, "HEALTH_WINDOW", .01):
      await client.start_server()
      try:
        await asyncio.wait_for(app[server.FEED].stopped.wait(), timeout=2)
        async with client.get("/api/state", headers=self.auth) as response:
          self.assertEqual(response.status, 503)
      finally:
        await client.close()

  async def test_public_page_contains_no_token_or_external_requests(self):
    async with self.client.get("/") as response:
      self.assertEqual(response.status, 200)
      self.assertIn("text/html", response.headers["Content-Type"])
      self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
      self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
      self.assertNotIn(self.token, await response.text())


class SetupTests(unittest.TestCase):
  def test_instance_lock_is_exclusive_and_released_on_exit(self):
    with tempfile.TemporaryDirectory() as tmp:
      lock = Path(tmp) / "service.lock"
      with server.singleton(lock):
        with self.assertRaisesRegex(RuntimeError, "already running"):
          with server.singleton(lock):
            self.fail("second process acquired the same lock")
      with server.singleton(lock):
        self.assertTrue(lock.is_file())

  def test_bind_requires_private_ipv4_literal(self):
    for good in ["127.0.0.1", "10.1.2.3", "192.168.50.2", "172.31.255.1"]:
      self.assertEqual(server.local_address(good), good)
    for invalid in ["0.0.0.0", "8.8.8.8", "localhost", "::1", "172.32.0.1", "224.0.0.1"]:
      with self.assertRaises(ValueError):
        server.local_address(invalid)

  def test_token_is_random_owner_only_and_idempotent(self):
    with tempfile.TemporaryDirectory() as directory:
      first = Path(directory) / "dash" / "token"
      second = Path(directory) / "other" / "token"
      server.init_token(first)
      initial = server.read_token(first)
      server.init_token(first)
      server.init_token(second)
      self.assertEqual(server.read_token(first), initial)
      self.assertNotEqual(server.read_token(second), initial)
      self.assertEqual(stat.S_IMODE(first.stat().st_mode), 0o600)
      self.assertEqual(stat.S_IMODE(first.parent.stat().st_mode), 0o700)
      first.chmod(0o644)
      with self.assertRaises(ValueError):
        server.read_token(first)
      alias = Path(directory) / "alias"
      alias.symlink_to(second)
      with self.assertRaises(OSError):
        server.read_token(alias)

  def test_invalid_token_is_never_replaced_silently(self):
    with tempfile.TemporaryDirectory() as directory:
      path = Path(directory) / "token"
      path.write_bytes(b"incomplete")
      path.chmod(0o600)
      with self.assertRaises(ValueError):
        server.init_token(path)
      self.assertEqual(path.read_bytes(), b"incomplete")

  def test_budget_window_and_memory_threshold(self):
    readings = iter([(1., 12.), (2.6, 12.), (2.6, 12.), (4.4, 12.), (6.1, 12.)])
    budget = server.Budget(lambda: next(readings), 0)
    for now in (1, 10, 20, 30):
      self.assertFalse(budget.exceeded(now))
    self.assertTrue(budget.exceeded(40))
    self.assertTrue(budget.exceeded(100))
    self.assertTrue(server.Budget(lambda: (0, 151), 0).exceeded(0))
    memory_readings = iter([(1., 12.), (1., 151.)])
    self.assertTrue(server.Budget(lambda: next(memory_readings), 0).exceeded(10))
