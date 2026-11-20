"""LAN sync server (FastAPI over HTTPS). Runs only while the user has enabled phone sync on the desktop."""

from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
import time
from collections import defaultdict
from typing import Callable

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from .. import __version__
from ..db.connection import Database, utcnow
from . import crypto
from .ingest import import_sync_payload

MAX_BODY = 48 * 1024 * 1024
PAIR_ATTEMPTS_PER_10MIN = 10


def lan_addresses() -> list[str]:
    addrs = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 80))  # TEST-NET address: no packet is sent, only selects the outbound interface
        addrs.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addrs.add(info[4][0])
    except OSError:
        pass
    return sorted(a for a in addrs if not a.startswith("127."))


def create_app(db: Database, on_imported: Callable[[dict], None]) -> FastAPI:
    app = FastAPI(title="VITALIS Sync", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    attempts: dict[str, list[float]] = defaultdict(list)

    def auth(authorization: str | None) -> dict:
        token = authorization[7:] if authorization and authorization.startswith("Bearer ") else None
        dev = crypto.authenticate(db, token)
        if not dev:
            raise HTTPException(status_code=401, detail="pairing_required")
        return dev

    async def body_json(request: Request) -> dict:
        cl = request.headers.get("content-length")
        if cl and int(cl) > MAX_BODY:
            raise HTTPException(status_code=413, detail="payload_too_large")
        raw = await request.body()
        if len(raw) > MAX_BODY:
            raise HTTPException(status_code=413, detail="payload_too_large")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="invalid_json")

    @app.get("/v1/hello")
    def hello():
        return {"app": "VITALIS", "version": __version__, "server_time": utcnow(),
                "demo_profile": bool(db.get_pref("is_demo", False))}

    @app.post("/v1/pair")
    async def pair(request: Request):
        ip = request.client.host if request.client else "?"
        now = time.time()
        attempts[ip] = [t for t in attempts[ip] if now - t < 600]
        if len(attempts[ip]) >= PAIR_ATTEMPTS_PER_10MIN:
            raise HTTPException(status_code=429, detail="too_many_attempts")
        attempts[ip].append(now)
        body = await body_json(request)
        code = str(body.get("code", ""))
        if not code.isdigit() or len(code) != 8 or not crypto.consume_pairing_code(db, code):
            raise HTTPException(status_code=403, detail="invalid_or_expired_code")
        device_id, token = crypto.new_device(db, str(body.get("device_name", "Android phone")))
        on_imported({"event": "device_paired", "device_id": device_id})
        return {"device_id": device_id, "token": token}

    @app.post("/v1/batches")
    async def batches(request: Request, authorization: str | None = Header(None)):
        dev = auth(authorization)
        payload = await body_json(request)
        try:
            summary = import_sync_payload(db, payload, "lan_sync", device=dev)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        on_imported({"event": "batch_imported", "device_id": dev["id"], "summary": summary})
        return {"status": "already_imported" if summary.get("already_imported") else "imported", "summary": summary}

    @app.post("/v1/availability")
    async def availability(request: Request, authorization: str | None = Header(None)):
        dev = auth(authorization)
        payload = await body_json(request)
        db.execute("INSERT INTO availability_reports(device_id, provider, received_at, report) VALUES (?,?,?,?)",
                   (dev["id"], str(payload.get("provider", "health_connect"))[:40], utcnow(),
                    json.dumps(payload)[:2_000_000]))
        on_imported({"event": "availability_report", "device_id": dev["id"]})
        return {"status": "stored"}

    @app.get("/v1/status")
    def status(authorization: str | None = Header(None)):
        dev = auth(authorization)
        last = db.query_one("SELECT id, finished_at, status FROM import_batches WHERE detail LIKE ? ORDER BY"
                            " started_at DESC LIMIT 1", (f'%"{dev["id"]}"%',))
        return {"device_id": dev["id"], "last_batch": last}

    @app.exception_handler(Exception)
    async def unhandled(request, exc):  # never leak internals to the network
        return JSONResponse(status_code=500, content={"detail": "internal_error"})

    return app


class SyncService:
    def __init__(self, db: Database, notify: Callable[[dict], None]):
        self.db = db
        self.notify = notify
        self._server = None
        self._thread: threading.Thread | None = None
        self.port: int | None = None
        self.hosts: list[str] = []
        self.fp: str | None = None

    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, port: int = 47821, bind: str = "0.0.0.0") -> dict:
        import uvicorn
        if self.running():
            return self.status()
        self.hosts = lan_addresses() or ["127.0.0.1"]
        cert_pem, key_pem, self.fp = crypto.ensure_tls(self.db, self.hosts + ["127.0.0.1"])
        td = tempfile.mkdtemp(prefix="vitalis-tls-")
        cpath, kpath = os.path.join(td, "c.pem"), os.path.join(td, "k.pem")
        fd = os.open(kpath, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(key_pem)
        with open(cpath, "wb") as fh:
            fh.write(cert_pem)
        app = create_app(self.db, self.notify)
        config = uvicorn.Config(app, host=bind, port=port, ssl_certfile=cpath, ssl_keyfile=kpath, log_level="warning",
                                access_log=False, lifespan="off", server_header=False, date_header=False)
        self._server = uvicorn.Server(config)
        self._server.install_signal_handlers = lambda: None  # type: ignore[assignment]
        self._thread = threading.Thread(target=self._server.run, name="vitalis-sync", daemon=True)
        self._thread.start()
        for _ in range(100):
            if self._server.started:
                break
            time.sleep(0.05)
        for p in (kpath, cpath):  # TLS material is loaded; remove the temporary files
            try:
                os.remove(p)
            except OSError:
                pass
        os.rmdir(td)
        if not self._server.started:
            self.stop()
            raise RuntimeError(f"could not start sync server on port {port} (in use or blocked)")
        self.port = port
        return self.status()

    def stop(self) -> dict:
        if self._server:
            self._server.should_exit = True
        if self._thread:
            self._thread.join(timeout=5)
        self._server, self._thread = None, None
        return self.status()

    def status(self) -> dict:
        return {"running": self.running(), "port": self.port, "hosts": self.hosts, "fingerprint": self.fp,
                "devices": self.db.query("SELECT id, name, created_at, last_seen, revoked_at FROM sync_devices"
                                         " ORDER BY created_at DESC")}

    def pairing_payload(self, desktop_name: str) -> dict:
        if not self.running():
            raise RuntimeError("start sync first")
        code, expires = crypto.new_pairing_code(self.db)
        qr = {"v": 1, "app": "vitalis", "name": desktop_name[:40], "hosts": self.hosts, "port": self.port,
              "fp": self.fp, "code": code, "fk": crypto.b64(crypto.file_key(self.db))}
        return {"qr_text": "vitalis:" + json.dumps(qr, separators=(",", ":")), "code": code, "expires_at": expires,
                "hosts": self.hosts, "port": self.port, "fingerprint": self.fp}
