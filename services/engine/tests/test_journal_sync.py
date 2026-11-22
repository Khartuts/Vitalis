"""Journal/experiment sync between phone and PC (vitalis.journal.v1): newer record wins, deletions travel."""
import os
import time
from datetime import date

from fastapi.testclient import TestClient

from vitalis_engine import journal_sync
from vitalis_engine.analytics import experiments
from vitalis_engine.db.connection import Database
from vitalis_engine.journal import delete_habit, ensure_builtins, get_day, set_day, upsert_habit
from vitalis_engine.sync import crypto
from vitalis_engine.sync.server import create_app

D = date(2026, 11, 20)


def _db(tmp_path, name):
    db = Database(tmp_path / f"{name}.db", os.urandom(32).hex())
    ensure_builtins(db)
    return db


def _exchange(phone, pc, cur):
    """One phone<->PC exchange as the app does it; returns the new cursors."""
    out = journal_sync.export_changes(phone, cur.get("sent"))
    journal_sync.import_changes(pc, out)
    back = journal_sync.export_changes(pc, cur.get("pc"))
    journal_sync.import_changes(phone, back)
    return {"sent": out["now"], "pc": back["now"]}


def _tick():
    time.sleep(1.1)  # change times have second resolution


def test_entries_both_ways_newer_wins_and_deletions(tmp_path):
    phone, pc = _db(tmp_path, "phone"), _db(tmp_path, "pc")
    cur = {}
    set_day(phone, D, {"coffee_cups": 2, "mood": 7}, note="phone note")
    cur = _exchange(phone, pc, cur)
    assert get_day(pc, D)["entries"]["coffee_cups"]["value_num"] == 2 and get_day(pc, D)["note"] == "phone note"
    _tick()
    set_day(pc, D, {"coffee_cups": 3})            # edited later on the PC
    cur = _exchange(phone, pc, cur)
    assert get_day(phone, D)["entries"]["coffee_cups"]["value_num"] == 3
    _tick()
    set_day(phone, D, {"mood": None})             # cleared on the phone -> deleted on the PC too
    cur = _exchange(phone, pc, cur)
    assert "mood" not in get_day(pc, D)["entries"]
    # Concurrent edits: the later one wins on both devices.
    _tick()
    set_day(pc, D, {"coffee_cups": 4})
    _tick()
    set_day(phone, D, {"coffee_cups": 5})
    cur = _exchange(phone, pc, cur)
    assert get_day(pc, D)["entries"]["coffee_cups"]["value_num"] == 5
    assert get_day(phone, D)["entries"]["coffee_cups"]["value_num"] == 5
    # A repeated exchange changes nothing (no ping-pong).
    again = journal_sync.import_changes(pc, journal_sync.export_changes(phone, None))
    assert again["entries"] == 0 and again["deleted_entries"] == 0


def test_custom_habits_and_experiments_travel(tmp_path):
    phone, pc = _db(tmp_path, "phone"), _db(tmp_path, "pc")
    upsert_habit(phone, {"key": "sauna", "label": "Sauna", "category": "custom", "value_type": "boolean"})
    set_day(phone, D, {"sauna": True})
    exp = experiments.create(pc, {"title": "Cooler bedroom", "hypothesis": "18 °C improves efficiency",
                                  "habit_key": "bedroom_temp_c", "condition": {"op": "<=", "value": 18},
                                  "outcome": "sleep_efficiency", "lag_days": 1, "design": "randomized_blocks",
                                  "start_date": "2026-11-21", "baseline_days": 4, "intervention_days": 14})
    cur = _exchange(phone, pc, {})
    assert get_day(pc, D)["entries"]["sauna"]["value_num"] == 1.0
    assert experiments.get(phone, exp["id"])["title"] == "Cooler bedroom"
    _tick()
    delete_habit(phone, "sauna")
    experiments.delete(pc, exp["id"])
    _exchange(phone, pc, cur)
    assert pc.query_one("SELECT key FROM journal_habits WHERE key='sauna'") is None
    assert phone.query_one("SELECT id FROM experiments WHERE id=?", (exp["id"],)) is None


def test_pc_endpoint_requires_pairing_and_exchanges(tmp_path):
    phone, pc = _db(tmp_path, "phone"), _db(tmp_path, "pc")
    events = []
    client = TestClient(create_app(pc, events.append))
    body = {"since": None, "changes": journal_sync.export_changes(phone, None)}
    assert client.post("/v1/journal/exchange", json=body).status_code == 401
    code, _ = crypto.new_pairing_code(pc)
    token = client.post("/v1/pair", json={"code": code, "device_name": "Galaxy"}).json()["token"]
    h = {"Authorization": f"Bearer {token}"}
    set_day(phone, D, {"stress": 6})
    set_day(pc, D, {"water_l": 2.0})
    r = client.post("/v1/journal/exchange", json={"since": None, "changes": journal_sync.export_changes(phone, None)}, headers=h)
    assert r.status_code == 200 and r.json()["applied"]["entries"] >= 1
    journal_sync.import_changes(phone, r.json()["changes"])
    assert get_day(phone, D)["entries"]["water_l"]["value_num"] == 2.0
    assert get_day(pc, D)["entries"]["stress"]["value_num"] == 6
    assert any(e["event"] == "journal_exchanged" for e in events)
    bad = client.post("/v1/journal/exchange", json={"since": None, "changes": {"schema": "x"}}, headers=h)
    assert bad.status_code == 422


def test_migration_3_upgrades_existing_journal(tmp_path):
    db = _db(tmp_path, "old")
    assert db.schema_version() >= 3
    assert all(h["updated_at"] for h in db.query("SELECT updated_at FROM journal_habits"))
