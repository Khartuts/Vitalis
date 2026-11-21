"""The in-process engine used by the Android app: same protocol as the stdio engine, over queues."""
import json
import os
import sys


def _call(eng, rid, method, params=None, timeout=120):
    eng.send(json.dumps({"id": rid, "method": method, "params": params or {}}))
    while True:
        line = eng.receive(timeout)
        assert line, f"no answer to {method}"
        msg = json.loads(line)
        if msg.get("id") == rid:
            return msg


def _start(tmp_path, demo=True, key=None):
    from vitalis_engine.embedded import EmbeddedEngine
    eng = EmbeddedEngine()
    eng.start(json.dumps({"type": "init", "db_path": str(tmp_path / "phone.db"), "key": key or os.urandom(32).hex(),
                          "demo": demo, "tz": "Europe/Zagreb", "profile_name": "Phone"}))
    return eng


def test_embedded_engine_full_round_trip_without_lan_server(tmp_path, monkeypatch):
    # The phone has no FastAPI/uvicorn: importing the LAN sync server must not be required.
    monkeypatch.setitem(sys.modules, "vitalis_engine.sync.server", None)
    key = os.urandom(32).hex()
    eng = _start(tmp_path, key=key)
    ready = json.loads(eng.receive(60))
    assert ready["type"] == "ready" and ready["demo"] is True
    assert _call(eng, 1, "system.status")["result"]["demo"] is True
    loaded = _call(eng, 2, "demo.load", {"days": 30})
    assert "error" not in loaded, loaded
    ov = _call(eng, 3, "overview.get", {"date": _call(eng, 4, "system.status")["result"]["today"]})
    assert "error" not in ov and ov["result"]["last_sleep"] is not None
    st = _call(eng, 5, "sync.status")["result"]
    assert st["available"] is False and st["running"] is False
    assert _call(eng, 6, "sync.start", {})["error"]["code"] == "not_available"
    assert eng.stop() == 0
    assert eng.receive(1) is None  # stream ended

    # Same file, wrong key: the engine refuses to start (encrypted database).
    bad = _start(tmp_path, key=os.urandom(32).hex())
    fatal = json.loads(bad.receive(60))
    assert fatal["type"] == "fatal"
    assert bad.receive(5) is None


def test_phone_local_health_connect_batch(tmp_path):
    """The phone imports its own Health Connect batches into its engine (same pipeline as the PC's LAN import)."""
    from conftest import hc
    eng = _start(tmp_path, demo=False)
    assert json.loads(eng.receive(60))["type"] == "ready"
    batch = {"schema": "vitalis.sync.v1", "batch_id": "3f0c9a2e-4b1d-4c6e-9a51-2d7f8e6b1c40", "source": "health_connect", "device": {"model": "SM-S921B"},
             "records": [hc("RestingHeartRateRecord", id="r1", time="2026-11-18T06:00:00Z", zoneOffset="+01:00", bpm=54)],
             "deletions": []}
    r = _call(eng, 1, "ingest.local_batch", {"payload": batch})["result"]
    assert r["inserted"] == 1
    again = _call(eng, 2, "ingest.local_batch", {"payload": batch})["result"]
    assert again.get("already_imported") is True
    bad = _call(eng, 3, "ingest.local_batch", {"payload": {"schema": "nope"}})
    assert bad["error"]["code"] in ("invalid_batch", "invalid_params")
    assert eng.stop() == 0
