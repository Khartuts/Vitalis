import hashlib
import json
import os
import socket
import ssl
import subprocess
import sys
import uuid

import pytest
from conftest import hc
from fastapi.testclient import TestClient

from vitalis_engine.sync import crypto
from vitalis_engine.sync.server import SyncService, create_app


def payload(batch_id=None, n=3):
    return {"schema": "vitalis.sync.v1", "batch_id": batch_id or str(uuid.uuid4()), "source": "health_connect",
            "device": {"model": "SM-S918B"}, "records": [
                hc("RestingHeartRateRecord", id=f"r{i}", time=f"2026-11-1{i}T06:00:00Z", zoneOffset="+01:00", bpm=55)
                for i in range(n)], "deletions": []}


def test_pairing_auth_import_replay_and_revocation(db):
    events = []
    client = TestClient(create_app(db, events.append))
    assert client.get("/v1/hello").json()["app"] == "VITALIS"
    assert client.post("/v1/batches", json=payload()).status_code == 401
    assert client.post("/v1/pair", json={"code": "12345678"}).status_code == 403
    code, _ = crypto.new_pairing_code(db)
    r = client.post("/v1/pair", json={"code": code, "device_name": "Galaxy S23"})
    assert r.status_code == 200
    token = r.json()["token"]
    assert client.post("/v1/pair", json={"code": code}).status_code == 403  # one-time code
    h = {"Authorization": f"Bearer {token}"}
    p = payload()
    r = client.post("/v1/batches", json=p, headers=h)
    assert r.status_code == 200 and r.json()["summary"]["inserted"] == 3
    r = client.post("/v1/batches", json=p, headers=h)
    assert r.json()["status"] == "already_imported"  # restart / retry safe
    assert client.post("/v1/batches", json={"schema": "x"}, headers=h).status_code == 422
    assert client.post("/v1/batches", content=b"{not json", headers=h).status_code == 400
    dev = db.query_one("SELECT id, token_sha256 FROM sync_devices")
    assert dev["token_sha256"] == hashlib.sha256(token.encode()).hexdigest()  # only hash stored
    db.execute("UPDATE sync_devices SET revoked_at='now'")
    assert client.post("/v1/batches", json=payload(), headers=h).status_code == 401
    assert any(e["event"] == "batch_imported" for e in events)


def test_pairing_rate_limit(db):
    client = TestClient(create_app(db, lambda e: None))
    codes = [client.post("/v1/pair", json={"code": "00000000"}).status_code for _ in range(12)]
    assert 429 in codes


def test_file_sync_encryption_and_tamper_detection(db):
    key = crypto.file_key(db)
    blob = crypto.encrypt_file_payload(key, json.dumps(payload()).encode())
    assert json.loads(crypto.decrypt_file_payload(key, blob))["schema"] == "vitalis.sync.v1"
    tampered = blob[:-1] + bytes([blob[-1] ^ 1])
    with pytest.raises(Exception):
        crypto.decrypt_file_payload(key, tampered)
    with pytest.raises(Exception):
        crypto.decrypt_file_payload(os.urandom(32), blob)


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_real_tls_listener_with_pinned_fingerprint(db):
    svc = SyncService(db, lambda e: None)
    port = _free_port()
    st = svc.start(port, bind="127.0.0.1")
    try:
        assert st["running"] and len(st["fingerprint"]) == 64
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # the phone pins the fingerprint instead of trusting a CA
        with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
            with ctx.wrap_socket(raw, server_hostname="vitalis.local") as tls:
                der = tls.getpeercert(binary_form=True)
                assert hashlib.sha256(der).hexdigest() == st["fingerprint"]
                assert tls.version() in ("TLSv1.2", "TLSv1.3")
                tls.sendall(b"GET /v1/hello HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
                data = tls.recv(4096)
                assert b"200" in data.split(b"\r\n")[0]
        with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:  # plain HTTP is refused
            raw.sendall(b"GET /v1/hello HTTP/1.1\r\nHost: x\r\n\r\n")
            resp = raw.recv(100)
            assert not resp.startswith(b"HTTP/1.1 200")
        qr = svc.pairing_payload("Test PC")
        assert qr["qr_text"].startswith("vitalis:") and json.loads(qr["qr_text"][8:])["fp"] == st["fingerprint"]
    finally:
        svc.stop()
    assert not svc.running()


def _engine(tmp_path, demo=True):
    p = subprocess.Popen([sys.executable, "-m", "vitalis_engine"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True)
    p.stdin.write(json.dumps({"type": "init", "db_path": str(tmp_path / "e.db"), "key": os.urandom(32).hex(),
                              "demo": demo}) + "\n")
    p.stdin.flush()
    return p


def _read_id(p, rid):
    while True:
        line = p.stdout.readline()
        assert line, p.stderr.read()
        msg = json.loads(line)
        if msg.get("id") == rid:
            return msg


def test_rpc_process_protocol(tmp_path):
    p = _engine(tmp_path)
    try:
        assert json.loads(p.stdout.readline())["type"] == "ready"
        for rid, req in enumerate([{"method": "system.status"}, {"method": "no.such"},
                                   {"method": "features.series", "params": {"start": "x", "end": "2026-01-01",
                                                                             "features": ["rhr"]}},
                                   {"method": "system.status", "params": {"unexpected": 1}}], start=1):
            p.stdin.write(json.dumps({"id": rid, **req}) + "\n")
            p.stdin.flush()
            r = _read_id(p, rid)
            if rid == 1:
                assert r["result"]["demo"] is True and r["result"]["counts"]["measurements"] == 0
            elif rid == 2:
                assert r["error"]["code"] == "method_not_found"
            else:
                assert r["error"]["code"] == "invalid_params"
        p.stdin.write(json.dumps({"id": 9, "method": "data.wipe", "params": {"confirm": "yes"}}) + "\n")
        p.stdin.flush()
        assert _read_id(p, 9)["error"]["code"] == "invalid_params"
    finally:
        p.stdin.close()  # EOF -> engine exits (process lifetime bound to the desktop app)
        assert p.wait(timeout=20) == 0


def test_rpc_wrong_key_is_fatal(tmp_path):
    p = _engine(tmp_path)
    json.loads(p.stdout.readline())
    p.stdin.close()
    p.wait(timeout=20)
    q = subprocess.Popen([sys.executable, "-m", "vitalis_engine"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True)
    out, _ = q.communicate(json.dumps({"type": "init", "db_path": str(tmp_path / "e.db"),
                                       "key": os.urandom(32).hex()}) + "\n", timeout=20)
    assert json.loads(out.splitlines()[0])["type"] == "fatal" and q.returncode == 2
