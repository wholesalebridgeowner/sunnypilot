"""Read-only RamPilot dashboard. Run explicitly on a trusted local IPv4 network."""
import argparse
import asyncio
import contextlib
import hmac
import ipaddress
import json
import logging
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time

from aiohttp import web, WSMsgType


HERE = Path(__file__).resolve().parent
DEFAULT_TOKEN_FILE = Path("/data/ram-diag/dash/token")
LOCK_PATH = Path("/dev/shm/rampilot-dashboard.service.lock")
PORT = 8765
PERIOD = 0.5
CACHE_MAX_AGE = 1.5
MAX_CLIENTS = 4
HEALTH_WINDOW = 10.
CPU_LIMIT_PCT = 15.
ANON_RSS_LIMIT_MIB = 150.
PROTOCOL = "rampilot.v1"
PRIVATE_NETWORKS = tuple(ipaddress.IPv4Network(s) for s in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8"))
LOG = logging.getLogger(__name__)


def local_address(value):
  try:
    address = ipaddress.IPv4Address(value)
  except ipaddress.AddressValueError:
    raise ValueError("use a numeric loopback or private LAN IPv4 address") from None
  if not any(address in network for network in PRIVATE_NETWORKS):
    raise ValueError("use a numeric loopback or private LAN IPv4 address")
  return str(address)


def read_token(path):
  flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
  with os.fdopen(os.open(path, flags), "rb") as handle:
    info = os.fstat(handle.fileno())
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
      raise ValueError("token must be an owner-only regular file belonging to the service account")
    value = handle.read(46)
  if not re.fullmatch(rb"[A-Za-z0-9_-]{43}\n?", value):
    raise ValueError("invalid dashboard token file")
  return value.rstrip(b"\n").decode("ascii")


def init_token(path):
  """Publish a complete 256-bit token atomically. Repeating setup keeps the original."""
  path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
  with tempfile.NamedTemporaryFile(dir=path.parent) as handle:
    handle.write(secrets.token_urlsafe(32).encode("ascii") + b"\n")
    handle.flush()
    os.fsync(handle.fileno())
    try:
      os.link(handle.name, path)
    except FileExistsError:
      pass
  read_token(path)


def resource_probe():
  # Match the advisor's Linux accounting, which excludes shared mapped pages.
  with open("/proc/self/status", encoding="ascii") as status:
    for line in status:
      if line.startswith("RssAnon:"):
        return time.process_time(), int(line.split()[1]) / 1024.
  raise RuntimeError("anonymous RSS unavailable")


class Budget:
  def __init__(self, probe, now):
    self.probe = probe
    self.checked = now
    self.cpu, memory = probe()
    self.cpu_high = False
    self.stopped = memory > ANON_RSS_LIMIT_MIB

  def exceeded(self, now):
    elapsed = now - self.checked
    if self.stopped or elapsed < HEALTH_WINDOW:
      return self.stopped
    cpu, memory = self.probe()
    high = 100 * (cpu - self.cpu) / elapsed > CPU_LIMIT_PCT
    self.stopped = memory > ANON_RSS_LIMIT_MIB or (high and self.cpu_high)
    self.cpu_high, self.checked, self.cpu = high, now, cpu
    return self.stopped


class Feed:
  def __init__(self, source, token):
    self.source = source
    self.token = token
    self.payload = None
    self.updated = 0.
    self.clients = {}
    self.stopped = asyncio.Event()

  def current(self):
    if self.stopped.is_set() or time.monotonic() - self.updated >= CACHE_MAX_AGE:
      return None
    return self.payload

  def sample(self):
    try:
      self.payload = json.dumps(self.source.snapshot(), separators=(",", ":"), allow_nan=False)
    except Exception:
      # A failing source must not keep presenting its last successful values.
      if self.payload is not None:
        LOG.warning("dashboard telemetry unavailable")
      self.payload = None
    self.updated = time.monotonic()
    for queue in self.clients.values():
      if queue.full():
        queue.get_nowait()
      queue.put_nowait(self.payload)

  async def monitor(self, budget):
    while True:
      await asyncio.sleep(PERIOD)
      try:
        exceeded = budget.exceeded(time.monotonic())
      except (OSError, ValueError, RuntimeError):
        exceeded = True
      if exceeded:
        LOG.warning("dashboard stopped by resource guard")
        self.stopped.set()
        return
      self.sample()


FEED = web.AppKey("feed", Feed)


@web.middleware
async def local_only(request, handler):
  transport = request.transport
  if transport is None:
    raise web.HTTPForbidden()
  peer = transport.get_extra_info("peername")
  listener = transport.get_extra_info("sockname")
  if not peer or not listener:
    raise web.HTTPForbidden()
  try:
    local_address(peer[0])
    host = local_address(listener[0])
  except ValueError:
    raise web.HTTPForbidden() from None
  # Accept only the numeric address of this interface. Ignore proxy headers.
  authority = host if listener[1] == 80 else f"{host}:{listener[1]}"
  if request.host not in (authority, f"{host}:{listener[1]}"):
    raise web.HTTPForbidden()
  origin = request.headers.get("Origin")
  if origin is not None and origin != "http://" + authority:
    raise web.HTTPForbidden()
  if request.query_string:
    raise web.HTTPBadRequest(text="Query parameters are unsupported")
  return await handler(request)


def token_matches(candidate, expected):
  return candidate.isascii() and hmac.compare_digest(candidate, expected)


async def index(request):
  authority = request.host
  return web.Response(body=(HERE / "index.html").read_bytes(), content_type="text/html", charset="utf-8", headers={
    "Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": f"default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                               f"connect-src 'self' ws://{authority}; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
  })


async def state(request):
  feed = request.app[FEED]
  if not token_matches(request.headers.get("Authorization", ""), "Bearer " + feed.token):
    raise web.HTTPUnauthorized(headers={"WWW-Authenticate": "Bearer"})
  payload = feed.current()
  if payload is None:
    raise web.HTTPServiceUnavailable(headers={"Cache-Control": "no-store"})
  return web.Response(text=payload, content_type="application/json", headers={"Cache-Control": "no-store"})


async def send_updates(ws, queue):
  while True:
    value = await queue.get()
    if value is None:
      await ws.close(code=1013, message=b"Telemetry unavailable")
      return
    try:
      await asyncio.wait_for(ws.send_str(value), timeout=2.)
    except asyncio.TimeoutError:
      await ws.close(code=1013, message=b"Slow connection")
      return


async def websocket(request):
  feed = request.app[FEED]
  protocols = [p.strip() for p in request.headers.get("Sec-WebSocket-Protocol", "").split(",")]
  if PROTOCOL not in protocols or not any(token_matches(p, "rp." + feed.token) for p in protocols):
    raise web.HTTPUnauthorized()
  payload = feed.current()
  if payload is None:
    raise web.HTTPServiceUnavailable()
  if len(feed.clients) >= MAX_CLIENTS:
    raise web.HTTPServiceUnavailable(text="Dashboard connections full")
  ws = web.WebSocketResponse(protocols=(PROTOCOL,), max_msg_size=128, compress=False, timeout=1., heartbeat=15.)
  queue = asyncio.Queue(maxsize=1)
  queue.put_nowait(payload)
  feed.clients[ws] = queue
  try:
    await ws.prepare(request)
    sender = asyncio.create_task(send_updates(ws, queue))
    try:
      async for msg in ws:
        if msg.type in (WSMsgType.TEXT, WSMsgType.BINARY):
          await ws.close(code=1008, message=b"Read-only feed")
          break
    finally:
      sender.cancel()
      with contextlib.suppress(asyncio.CancelledError, ConnectionError):
        await sender
  finally:
    feed.clients.pop(ws, None)
  return ws


def create_app(source, token, *, probe=resource_probe):
  """One sampler serves all HTTP and WebSocket clients, including replay clients."""
  feed = Feed(source, token)
  app = web.Application(middlewares=[local_only], client_max_size=1024)
  app[FEED] = feed

  async def sampling(_app):
    budget = Budget(probe, time.monotonic())
    if budget.stopped:
      raise RuntimeError("dashboard startup memory limit exceeded")
    feed.sample()
    task = asyncio.create_task(feed.monitor(budget))
    try:
      yield
    finally:
      task.cancel()
      with contextlib.suppress(asyncio.CancelledError):
        await task

  async def shutdown(_app):
    feed.stopped.set()
    await asyncio.gather(*(ws.close(code=1001) for ws in list(feed.clients)), return_exceptions=True)

  app.cleanup_ctx.append(sampling)
  app.on_shutdown.append(shutdown)
  app.router.add_get("/", index)
  app.router.add_get("/api/state", state)
  app.router.add_get("/ws", websocket)
  return app


@contextlib.contextmanager
def singleton(path):
  import fcntl  # the deployed service runs on Linux; injected replay apps do not need a lock
  flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC
  with os.fdopen(os.open(path, flags, 0o600), "rb") as handle:
    info = os.fstat(handle.fileno())
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
      raise RuntimeError("invalid dashboard instance lock")
    try:
      fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
      raise RuntimeError("dashboard already running") from None
    yield


def require_companion():
  home = Path(os.environ.get("ADVISOR_HOME", "/data/ram-diag/advisor"))
  if not all((home / name).is_file() for name in ("agent.py", "dash_relay.py", "install_agent.py", "start_agent.sh")):
    raise RuntimeError("shared dashboard requires the relay-enabled advisor installation")
  status = subprocess.run([sys.executable, str(home / "install_agent.py"), "status"], timeout=3,
                          capture_output=True, check=False)
  if status.returncode or status.stdout.strip() != b"installed":
    raise RuntimeError("shared dashboard requires the installed advisor launcher")


async def serve(args):
  from .shared import SharedSource
  require_companion()
  with singleton(LOCK_PATH), socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock, contextlib.ExitStack() as relay:
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((args.bind, args.port))
    sock.listen(8)
    sock.setblocking(False)
    if args.init_token:
      init_token(args.token_file)
    token = read_token(args.token_file)
    app = create_app(relay.enter_context(SharedSource()), token)
    runner = web.AppRunner(app, access_log=None, shutdown_timeout=3., keepalive_timeout=5.)
    await runner.setup()
    try:
      await web.SockSite(runner, sock, backlog=8).start()
      loop = asyncio.get_running_loop()
      loop.add_signal_handler(signal.SIGTERM, app[FEED].stopped.set)
      await app[FEED].stopped.wait()
    finally:
      await runner.cleanup()


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  commands = parser.add_subparsers(dest="action", required=True)
  init = commands.add_parser("init-token")
  run = commands.add_parser("serve")
  for command in (init, run):
    command.add_argument("--token-file", type=Path, default=DEFAULT_TOKEN_FILE)
  run.add_argument("--bind", type=local_address, required=True, help="numeric loopback or trusted private LAN address")
  run.add_argument("--port", type=int, default=PORT)
  run.add_argument("--init-token", action="store_true", help="create the owner-only pairing token on first start")
  args = parser.parse_args()
  if args.action == "init-token":
    init_token(args.token_file)
  else:
    if not 1 <= args.port <= 65535:
      parser.error("invalid TCP port")
    asyncio.run(serve(args))


if __name__ == "__main__":
  main()
