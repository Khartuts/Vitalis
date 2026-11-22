"""Journal and experiment sync between the phone and the PC (vitalis.journal.v1).

Each device sends the records it changed since the last exchange: habit definitions, journal entries, day notes,
experiments, and deletion markers for entries, custom habits and experiments. A record is applied only if it is newer
than the local copy (last writer wins, by each record's change time); applied records keep the origin's change time,
so an exchange never ping-pongs. Health data are not part of this exchange (they come from Health Connect on each
device, and from the phone to the PC through the batch sync).
"""

from __future__ import annotations

import json
from datetime import date

from .analytics import experiments
from .db.connection import Database, utcnow
from .journal import delete_habit, list_habits, upsert_habit

SCHEMA = "vitalis.journal.v1"
EPOCH = "1970-01-01T00:00:00Z"
_EXP_COLS = ("id", "title", "hypothesis", "habit_key", "condition", "outcome", "lag_days", "design", "start_date",
             "baseline_days", "intervention_days", "block_days", "seed", "schedule", "status", "created_at", "updated_at")


def export_changes(db: Database, since: str | None) -> dict:
    s = since or EPOCH
    habits = [{k: h[k] for k in ("key", "label", "category", "value_type", "unit", "options", "private", "active")}
              | {"updated_at": h["updated_at"]}
              for h in (dict(x) for x in db.query("SELECT * FROM journal_habits WHERE updated_at > ?", (s,)))]
    for h in habits:
        h["options"] = json.loads(h["options"]) if h["options"] else None
        h["private"], h["active"] = bool(h["private"]), bool(h["active"])
    return {
        "schema": SCHEMA, "now": utcnow(), "since": s,
        "habits": habits,
        "habit_tombstones": db.query("SELECT key, deleted_at FROM habit_tombstones WHERE deleted_at > ?", (s,)),
        "entries": db.query("SELECT local_date, habit_key, value_num, value_text, updated_at FROM journal_entries"
                            " WHERE updated_at > ?", (s,)),
        "entry_tombstones": db.query("SELECT local_date, habit_key, deleted_at FROM journal_tombstones"
                                     " WHERE deleted_at > ?", (s,)),
        "notes": db.query("SELECT local_date, note, tags, updated_at FROM day_notes WHERE updated_at > ?", (s,)),
        "experiments": db.query(f"SELECT {', '.join(_EXP_COLS)} FROM experiments WHERE updated_at > ?", (s,)),
        "experiment_tombstones": db.query("SELECT id, deleted_at FROM experiment_tombstones WHERE deleted_at > ?", (s,)),
    }


def _newer(incoming: str | None, local: str | None) -> bool:
    return bool(incoming) and (local is None or incoming > local)


def import_changes(db: Database, ch: dict) -> dict:
    if ch.get("schema") != SCHEMA:
        raise ValueError("not a vitalis.journal.v1 exchange")
    n = {"habits": 0, "entries": 0, "deleted_entries": 0, "notes": 0, "experiments": 0, "deleted": 0, "skipped": 0}
    dates: set[str] = set()
    local_h = {h["key"]: h for h in db.query("SELECT key, builtin, updated_at FROM journal_habits")}
    for h in ch.get("habits") or []:
        if _newer(h["updated_at"], (local_h.get(h["key"]) or {}).get("updated_at")):
            upsert_habit(db, h)
            n["habits"] += 1
        else:
            n["skipped"] += 1
    for t in ch.get("habit_tombstones") or []:
        cur = local_h.get(t["key"])
        if cur and not cur["builtin"] and _newer(t["deleted_at"], cur["updated_at"]):
            delete_habit(db, t["key"], at=t["deleted_at"])
            n["deleted"] += 1
    known = {h["key"] for h in list_habits(db)}
    with db.transaction() as conn:
        for e in ch.get("entries") or []:
            if e["habit_key"] not in known:
                n["skipped"] += 1
                continue
            cur = conn.execute("SELECT updated_at FROM journal_entries WHERE local_date=? AND habit_key=?",
                               (e["local_date"], e["habit_key"])).fetchone()
            tomb = conn.execute("SELECT deleted_at FROM journal_tombstones WHERE local_date=? AND habit_key=?",
                                (e["local_date"], e["habit_key"])).fetchone()
            local = max([x[0] for x in (cur, tomb) if x], default=None)
            if not _newer(e["updated_at"], local):
                n["skipped"] += 1
                continue
            conn.execute("INSERT INTO journal_entries(local_date, habit_key, value_num, value_text, updated_at)"
                         " VALUES (?,?,?,?,?) ON CONFLICT(local_date, habit_key) DO UPDATE SET value_num=excluded.value_num,"
                         " value_text=excluded.value_text, updated_at=excluded.updated_at",
                         (e["local_date"], e["habit_key"], e["value_num"], e["value_text"], e["updated_at"]))
            conn.execute("DELETE FROM journal_tombstones WHERE local_date=? AND habit_key=?", (e["local_date"], e["habit_key"]))
            n["entries"] += 1
            dates.add(e["local_date"])
        for t in ch.get("entry_tombstones") or []:
            cur = conn.execute("SELECT updated_at FROM journal_entries WHERE local_date=? AND habit_key=?",
                               (t["local_date"], t["habit_key"])).fetchone()
            if cur and _newer(t["deleted_at"], cur[0]):
                conn.execute("DELETE FROM journal_entries WHERE local_date=? AND habit_key=?", (t["local_date"], t["habit_key"]))
                n["deleted_entries"] += 1
                dates.add(t["local_date"])
            if cur is None or _newer(t["deleted_at"], cur[0]):
                conn.execute("INSERT INTO journal_tombstones(local_date, habit_key, deleted_at) VALUES (?,?,?) ON CONFLICT"
                             "(local_date, habit_key) DO UPDATE SET deleted_at=max(deleted_at, excluded.deleted_at)",
                             (t["local_date"], t["habit_key"], t["deleted_at"]))
        for d in ch.get("notes") or []:
            cur = conn.execute("SELECT updated_at FROM day_notes WHERE local_date=?", (d["local_date"],)).fetchone()
            if _newer(d["updated_at"], cur[0] if cur else None):
                conn.execute("INSERT INTO day_notes(local_date, note, tags, updated_at) VALUES (?,?,?,?) ON CONFLICT"
                             "(local_date) DO UPDATE SET note=excluded.note, tags=excluded.tags, updated_at=excluded.updated_at",
                             (d["local_date"], d["note"], d["tags"], d["updated_at"]))
                n["notes"] += 1
        for x in ch.get("experiments") or []:
            cur = conn.execute("SELECT updated_at FROM experiments WHERE id=?", (x["id"],)).fetchone()
            gone = conn.execute("SELECT deleted_at FROM experiment_tombstones WHERE id=?", (x["id"],)).fetchone()
            if gone and gone[0] >= x["updated_at"]:
                n["skipped"] += 1
                continue
            if _newer(x["updated_at"], cur[0] if cur else None):
                conn.execute(f"INSERT OR REPLACE INTO experiments({', '.join(_EXP_COLS)}) VALUES "
                             f"({','.join('?' * len(_EXP_COLS))})", tuple(x[c] for c in _EXP_COLS))
                n["experiments"] += 1
    for t in ch.get("experiment_tombstones") or []:
        cur = db.query_one("SELECT updated_at FROM experiments WHERE id=?", (t["id"],))
        if cur is None or _newer(t["deleted_at"], cur["updated_at"]):
            experiments.delete(db, t["id"], at=t["deleted_at"])
            n["deleted"] += 1
    n["changed_dates"] = sorted(dates)
    return n


def date_range(n: dict) -> tuple[date, date] | None:
    d = n.get("changed_dates") or []
    return (date.fromisoformat(d[0]), date.fromisoformat(d[-1])) if d else None
