"""Small authenticated Comma outbound relay. No vehicle control endpoints."""
import hmac
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ADMIN_TOKEN = os.environ.get("BRIDGE_ADMIN_TOKEN", "")
AGENT_TOKEN = os.environ.get("BRIDGE_AGENT_TOKEN", "")
DB_PATH = os.environ.get("BRIDGE_DB_PATH", "/data/ram_bridge.sqlite3")
MAX_BODY = 2048
LOCK = threading.RLock()

def validate_command(data):
    if not isinstance(data, dict):
        raise ValueError("Expected object")
    op = data.get("op")
    if op == "ping" and set(data) == {"op"}:
        return {"op": "ping"}
    if op == "read_setting" and data.get("name") == "AutoLaneChangeTimer" and set(data) == {"op", "name"}:
        return {"op": op, "name": "AutoLaneChangeTimer"}
    if op == "write_marker" and set(data) == {"op", "value"}:
        val = data.get("value")
        if isinstance(val, str) and re.fullmatch(r"[A-Za-z0-9._-]{1,64}", val):
            return {"op": op, "value": val}
    raise ValueError("Only ping, read_setting(AutoLaneChangeTimer), or write_marker are allowed")

def db():
    con = sqlite3.connect(DB_PATH, timeout=5)
    con.row_factory = sqlite3.Row
    return con

def initialize():
    if len(ADMIN_TOKEN) < 32 or len(AGENT_TOKEN) < 32 or hmac.compare_digest(ADMIN_TOKEN, AGENT_TOKEN):
        raise RuntimeError("Two distinct 32+ character BRIDGE_ADMIN_TOKEN and BRIDGE_AGENT_TOKEN secrets required")
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS jobs
          (id TEXT PRIMARY KEY, command TEXT NOT NULL, state TEXT NOT NULL,
           created REAL NOT NULL, expires REAL NOT NULL, claimed REAL,
           result TEXT)""")
        con.execute("""CREATE TABLE IF NOT EXISTS device
          (id INTEGER PRIMARY KEY CHECK (id = 1), last_seen REAL NOT NULL, details TEXT NOT NULL)""")

class Handler(BaseHTTPRequestHandler):
    server_version = "RamDiagnosticBridge/0.1"

    def log_message(self, fmt, *args):
        # Do not log tokens, request bodies, location, or device parameters.
        print("%s %s" % (self.address_string(), fmt % args), flush=True)

    def respond(self, status, body):
        raw = json.dumps(body, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def authorized(self, role):
        expected = ADMIN_TOKEN if role == "admin" else AGENT_TOKEN
        token = self.headers.get("Authorization", "")
        if not hmac.compare_digest(token, "Bearer " + expected):
            self.respond(401, {"error": "unauthorized"})
            return False
        return True

    def body_json(self):
        length = self.headers.get("Content-Length", "")
        if not length.isdigit() or not 0 < int(length) <= MAX_BODY:
            raise ValueError("Invalid body length")
        value = json.loads(self.rfile.read(int(length)))
        if not isinstance(value, dict):
            raise ValueError("Expected JSON object")
        return value

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            return self.respond(200, {"ok": True, "device_connection": "requires_installed_agent"})
        if path == "/v1/admin/status":
            if not self.authorized("admin"): return
            with LOCK, db() as con:
                dev = con.execute("SELECT last_seen, details FROM device WHERE id=1").fetchone()
                queued = con.execute("SELECT count(*) FROM jobs WHERE state IN ('queued','claimed')").fetchone()[0]
            return self.respond(200, {"last_seen": dev["last_seen"] if dev else None,
                                      "details": json.loads(dev["details"]) if dev else None,
                                      "pending_jobs": queued})
        if path.startswith("/v1/admin/jobs/"):
            if not self.authorized("admin"): return
            job_id = path.rsplit("/", 1)[-1]
            if not re.fullmatch(r"[0-9a-f-]{36}", job_id):
                return self.respond(404, {"error": "not found"})
            with LOCK, db() as con:
                row = con.execute("SELECT id,command,state,created,expires,result FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row: return self.respond(404, {"error": "not found"})
            return self.respond(200, {"id": row["id"], "command": json.loads(row["command"]),
                                      "state": row["state"], "created": row["created"],
                                      "expires": row["expires"],
                                      "result": json.loads(row["result"]) if row["result"] else None})
        if path == "/v1/agent/poll":
            if not self.authorized("agent"): return
            now = time.time()
            with LOCK, db() as con:
                con.execute("BEGIN IMMEDIATE")
                con.execute("UPDATE jobs SET state='expired' WHERE state IN ('queued','claimed') AND expires < ?", (now,))
                con.execute("UPDATE jobs SET state='queued', claimed=NULL WHERE state='claimed' AND claimed < ?", (now-40,))
                row = con.execute("SELECT id, command FROM jobs WHERE state='queued' ORDER BY created LIMIT 1").fetchone()
                if row:
                    con.execute("UPDATE jobs SET state='claimed', claimed=? WHERE id=?", (now, row["id"]))
            return self.respond(200, {"job": {"id": row["id"], "command": json.loads(row["command"])} if row else None})
        return self.respond(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/v1/admin/jobs":
            if not self.authorized("admin"): return
            try:
                cmd = validate_command(self.body_json())
            except (ValueError, json.JSONDecodeError) as exc:
                return self.respond(400, {"error": str(exc)})
            job_id, now = str(uuid.uuid4()), time.time()
            with LOCK, db() as con:
                con.execute("INSERT INTO jobs (id,command,state,created,expires) VALUES (?,?,?,?,?)",
                            (job_id, json.dumps(cmd), "queued", now, now + 120))
            return self.respond(201, {"id": job_id, "state": "queued", "expires": now + 120})
        if path == "/v1/agent/heartbeat":
            if not self.authorized("agent"): return
            try:
                detail = self.body_json()
                if set(detail) != {"agent_version", "offroad"} or type(detail["offroad"]) is not bool or not isinstance(detail["agent_version"], str) or len(detail["agent_version"]) > 20:
                    raise ValueError("Invalid heartbeat")
            except (ValueError, json.JSONDecodeError) as exc:
                return self.respond(400, {"error": str(exc)})
            with LOCK, db() as con:
                con.execute("INSERT INTO device (id,last_seen,details) VALUES (1,?,?) ON CONFLICT(id) DO UPDATE SET last_seen=excluded.last_seen,details=excluded.details",
                            (time.time(), json.dumps(detail)))
            return self.respond(200, {"ok": True})
        if re.fullmatch(r"/v1/agent/jobs/[0-9a-f-]{36}/result", path):
            if not self.authorized("agent"): return
            try:
                payload = self.body_json()
                if set(payload) != {"ok", "result"} or type(payload["ok"]) is not bool or not isinstance(payload["result"], dict) or len(json.dumps(payload["result"])) > 1024:
                    raise ValueError("Invalid result")
            except (ValueError, json.JSONDecodeError) as exc:
                return self.respond(400, {"error": str(exc)})
            job_id = path.split("/")[-2]
            with LOCK, db() as con:
                cur = con.execute("UPDATE jobs SET state=?, result=? WHERE id=? AND state='claimed' AND expires>=?",
                                  ("done" if payload["ok"] else "failed", json.dumps(payload), job_id, time.time()))
            return self.respond(200 if cur.rowcount else 409, {"ok": bool(cur.rowcount)})
        return self.respond(404, {"error": "not found"})

if __name__ == "__main__":
    initialize()
    port = int(os.environ.get("PORT", "8080"))
    print(f"Diagnostic relay listening on {port}; no CAN/steering endpoints", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
