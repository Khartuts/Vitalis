import os
from datetime import date, datetime, timezone

import pytest

from vitalis_engine.db.backup import create_backup, restore_backup
from vitalis_engine.db.connection import Database, DatabaseError
from vitalis_engine.model.units import UnitError, convert
from vitalis_engine.timeutil import day_bounds_utc, local_date, parse_instant, parse_offset_minutes


def test_database_is_encrypted_and_rejects_wrong_key(tmp_path):
    key = os.urandom(32).hex()
    db = Database(tmp_path / "a.db", key)
    db.set_pref("secret", "resting heart rate 55")
    db.close()
    raw = (tmp_path / "a.db").read_bytes()
    assert not raw.startswith(b"SQLite format 3")
    assert b"resting heart rate" not in raw
    with pytest.raises(DatabaseError):
        Database(tmp_path / "a.db", os.urandom(32).hex())
    assert Database(tmp_path / "a.db", key).get_pref("secret") == "resting heart rate 55"


def test_invalid_key_format_rejected(tmp_path):
    with pytest.raises(DatabaseError):
        Database(tmp_path / "b.db", "not-hex")


def test_migrations_idempotent(db):
    from vitalis_engine.db.schema import MIGRATIONS
    assert db.schema_version() == max(v for v, _, _ in MIGRATIONS)
    assert db.migrate() == 0


def test_upgrade_from_v1_database(tmp_path):
    """A database created by schema v1 is upgraded in place without data loss."""
    import vitalis_engine.db.connection as conn_mod
    from vitalis_engine.db.schema import MIGRATIONS
    key = os.urandom(32).hex()
    orig = conn_mod.MIGRATIONS
    conn_mod.MIGRATIONS = MIGRATIONS[:1]
    try:
        old = Database(tmp_path / "old.db", key)
        old.set_pref("kept", True)
        assert old.schema_version() == 1
        old.close()
    finally:
        conn_mod.MIGRATIONS = orig
    new = Database(tmp_path / "old.db", key)
    assert new.schema_version() == 2 and new.get_pref("kept") is True
    cols = [r["name"] for r in new.query("PRAGMA table_info(sleep_sessions)")]
    assert "end_offset_min" in cols


def test_backup_restore_roundtrip_and_wrong_passphrase(db, tmp_path):
    db.set_pref("x", [1, 2, 3])
    info = create_backup(db, tmp_path / "b.vitalis-backup", "a long passphrase")
    assert info["bytes"] > 0
    k2 = os.urandom(32).hex()
    restore_backup(tmp_path / "b.vitalis-backup", "a long passphrase", tmp_path / "r.db", k2)
    assert Database(tmp_path / "r.db", k2).get_pref("x") == [1, 2, 3]
    with pytest.raises(DatabaseError):
        restore_backup(tmp_path / "b.vitalis-backup", "the wrong passphrase", tmp_path / "r2.db", k2)
    with pytest.raises(ValueError):
        create_backup(db, tmp_path / "c", "short")


def test_offsets_and_instants():
    assert parse_offset_minutes("+01:00") == 60
    assert parse_offset_minutes("UTC+0200") == 120
    assert parse_offset_minutes("-05:30") == -330
    assert parse_offset_minutes("3600s") == 60
    assert parse_offset_minutes(None) is None
    assert parse_instant("2026-03-29T01:30:00Z") == datetime(2026, 3, 29, 1, 30, tzinfo=timezone.utc)
    assert parse_instant(1790000000000) == datetime.fromtimestamp(1790000000, tz=timezone.utc)
    with pytest.raises(ValueError):
        parse_instant("2026-01-01T10:00:00")  # naive timestamps are rejected


def test_dst_day_lengths_zagreb():
    a, b = day_bounds_utc(date(2026, 3, 29))   # spring forward
    assert (b - a).total_seconds() == 23 * 3600
    a, b = day_bounds_utc(date(2026, 10, 25))  # fall back
    assert (b - a).total_seconds() == 25 * 3600


def test_local_date_uses_original_offset_then_display_tz():
    t = datetime(2026, 7, 1, 22, 30, tzinfo=timezone.utc)
    assert local_date(t, 120) == date(2026, 7, 2)          # recorded in CEST
    assert local_date(t, -300) == date(2026, 7, 1)         # recorded in New York
    assert local_date(t, None) == date(2026, 7, 2)         # inferred with Europe/Zagreb


def test_unit_conversions():
    assert convert(70000, "g", "kg") == pytest.approx(70.0)
    assert convert(5.5, "km", "m") == pytest.approx(5500)
    assert convert(98.6, "°F", "°C") == pytest.approx(37.0)
    assert convert(90, "mg/dL", "mmol/L") == pytest.approx(4.996, rel=1e-3)
    assert convert(0.96, "fraction", "%") == pytest.approx(96)
    with pytest.raises(UnitError):
        convert(60, "bpm", "ms")  # incompatible measurements are never reinterpreted
