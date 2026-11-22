"""Habit Journal: habit definitions (built-in + custom) and daily entries."""

from __future__ import annotations

import json
import re
from datetime import date

from .db.connection import Database, utcnow

BUILTIN_HABITS = [
    # key, label, category, type, unit, options
    ("bedtime_logged", "Bedtime (self-reported)", "sleep", "time", None, None),
    ("wake_logged", "Wake-up time (self-reported)", "sleep", "time", None, None),
    ("consistent_schedule", "Kept a consistent sleep schedule", "sleep", "boolean", None, None),
    ("nap", "Daytime nap", "sleep", "boolean", None, None),
    ("coffee_cups", "Coffee", "caffeine", "number", "cups", {"min": 0, "max": 15, "step": 1}),
    ("caffeine_last_time", "Last caffeine", "caffeine", "time", None, None),
    ("caffeine_after_lunch", "Caffeine after lunch", "caffeine", "boolean", None, None),
    ("water_l", "Water", "hydration", "number", "L", {"min": 0, "max": 8, "step": 0.25}),
    ("soft_drinks", "Carbonated soft drinks", "hydration", "number", "glasses", {"min": 0, "max": 10, "step": 1}),
    ("sugary_drinks", "Sugary drinks", "hydration", "number", "glasses", {"min": 0, "max": 10, "step": 1}),
    ("breakfast", "Had breakfast", "nutrition", "boolean", None, None),
    ("last_meal_time", "Last meal", "nutrition", "time", None, None),
    ("late_dinner", "Late dinner (within 3 h of bed)", "nutrition", "boolean", None, None),
    ("late_snack", "Late-night snack", "nutrition", "boolean", None, None),
    ("vegetables", "Vegetable servings", "nutrition", "number", "servings", {"min": 0, "max": 15, "step": 1}),
    ("walk", "Went for a walk", "activity", "boolean", None, None),
    ("exercise", "Exercised", "activity", "boolean", None, None),
    ("stretching", "Stretching", "activity", "boolean", None, None),
    ("sedentary_long", "Mostly sedentary day", "activity", "boolean", None, None),
    ("alcohol_units", "Alcohol", "lifestyle", "number", "units", {"min": 0, "max": 20, "step": 1}),
    ("smoking", "Smoked", "lifestyle", "boolean", None, None),
    ("outdoors_min", "Time outdoors", "lifestyle", "number", "min", {"min": 0, "max": 720, "step": 15}),
    ("meditation", "Meditation", "lifestyle", "boolean", None, None),
    ("breathing", "Breathing exercise", "lifestyle", "boolean", None, None),
    ("screen_before_bed", "Screens in the hour before bed", "lifestyle", "boolean", None, None),
    ("bedroom_temp_c", "Bedroom temperature at bedtime", "environment", "number", "°C", {"min": 10, "max": 32, "step": 0.5}),
    ("mood", "Mood", "experience", "scale", "1–10", {"min": 1, "max": 10}),
    ("stress", "Perceived stress", "experience", "scale", "1–10", {"min": 1, "max": 10}),
    ("energy", "Perceived energy", "experience", "scale", "1–10", {"min": 1, "max": 10}),
    ("focus", "Focus", "experience", "scale", "1–10", {"min": 1, "max": 10}),
    ("productivity", "Productivity", "experience", "scale", "1–10", {"min": 1, "max": 10}),
    ("sleepiness", "Daytime sleepiness", "experience", "scale", "1–10", {"min": 1, "max": 10}),
    ("workload", "Workload", "experience", "category", None, {"choices": ["light", "normal", "heavy", "day off"]}),
]

VALUE_TYPES = ("boolean", "number", "time", "category", "scale", "text")
_KEY = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def ensure_builtins(db: Database) -> None:
    now = utcnow()
    for i, (key, label, cat, vt, unit, opts) in enumerate(BUILTIN_HABITS):
        # Built-ins start with the epoch as change time, so any real edit on either device wins in a journal sync.
        db.execute("INSERT OR IGNORE INTO journal_habits(key, label, category, value_type, unit, options, private,"
                   " active, sort_order, builtin, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,1,?,?)",
                   (key, label, cat, vt, unit, json.dumps(opts) if opts else None,
                    1 if key in ("smoking", "alcohol_units") else 0, 1, i, now, "1970-01-01T00:00:00Z"))


def list_habits(db: Database, include_inactive: bool = True) -> list[dict]:
    rows = db.query("SELECT * FROM journal_habits" + ("" if include_inactive else " WHERE active=1") +
                    " ORDER BY sort_order, label")
    for r in rows:
        r["options"] = json.loads(r["options"]) if r["options"] else None
        r["private"] = bool(r["private"])
        r["active"] = bool(r["active"])
        r["builtin"] = bool(r["builtin"])
    return rows


def upsert_habit(db: Database, h: dict) -> dict:
    key = h["key"]
    if not _KEY.match(key):
        raise ValueError("habit key must be lowercase letters, digits and underscores")
    if h["value_type"] not in VALUE_TYPES:
        raise ValueError("invalid value_type")
    existing = db.query_one("SELECT builtin, sort_order FROM journal_habits WHERE key=?", (key,))
    order = existing["sort_order"] if existing else 1000
    now = h.get("updated_at") or utcnow()  # sync passes the origin's change time
    db.execute("INSERT INTO journal_habits(key, label, category, value_type, unit, options, private, active, sort_order,"
               " builtin, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,0,?,?) ON CONFLICT(key) DO UPDATE SET"
               " label=excluded.label, category=excluded.category, unit=excluded.unit, options=excluded.options,"
               " private=excluded.private, active=excluded.active, updated_at=excluded.updated_at",
               (key, h["label"], h.get("category", "custom"), h["value_type"], h.get("unit"),
                json.dumps(h.get("options")) if h.get("options") else None, 1 if h.get("private") else 0,
                0 if h.get("active") is False else 1, order, now, now))
    db.execute("DELETE FROM habit_tombstones WHERE key=?", (key,))
    return next(x for x in list_habits(db) if x["key"] == key)


def delete_habit(db: Database, key: str, at: str | None = None) -> None:
    now = at or utcnow()
    row = db.query_one("SELECT builtin FROM journal_habits WHERE key=?", (key,))
    if row and row["builtin"]:
        db.execute("UPDATE journal_habits SET active=0, updated_at=? WHERE key=?", (now, key))
    else:
        db.execute("DELETE FROM journal_habits WHERE key=?", (key,))
        db.execute("INSERT INTO habit_tombstones(key, deleted_at) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET"
                   " deleted_at=excluded.deleted_at", (key, now))


def get_day(db: Database, d: date) -> dict:
    entries = db.query("SELECT habit_key, value_num, value_text, note, tags, updated_at FROM journal_entries WHERE"
                       " local_date=?", (d.isoformat(),))
    note = db.query_one("SELECT note, tags FROM day_notes WHERE local_date=?", (d.isoformat(),))
    return {"date": d.isoformat(), "entries": {e["habit_key"]: e for e in entries},
            "note": note["note"] if note else None, "tags": json.loads(note["tags"]) if note and note["tags"] else []}


def _validate(habit: dict, value) -> tuple[float | None, str | None]:
    vt = habit["value_type"]
    opts = habit["options"] or {}
    if value is None or value == "":
        return None, None
    if vt == "boolean":
        if not isinstance(value, bool) and value not in (0, 1):
            raise ValueError(f"{habit['key']}: expected true/false")
        return float(bool(value)), None
    if vt in ("number", "scale"):
        v = float(value)
        if "min" in opts and v < opts["min"] or "max" in opts and v > opts["max"]:
            raise ValueError(f"{habit['key']}: value {v} outside {opts.get('min')}–{opts.get('max')}")
        return v, None
    if vt == "time":
        if not isinstance(value, str) or not _TIME.match(value):
            raise ValueError(f"{habit['key']}: expected HH:MM")
        return None, value
    if vt == "category":
        if opts.get("choices") and value not in opts["choices"]:
            raise ValueError(f"{habit['key']}: expected one of {opts['choices']}")
        return None, str(value)
    return None, str(value)[:2000]


def set_day(db: Database, d: date, values: dict, note: str | None = None, tags: list[str] | None = None) -> dict:
    habits = {h["key"]: h for h in list_habits(db)}
    now = utcnow()
    with db.transaction() as conn:
        for key, val in values.items():
            if key not in habits:
                raise ValueError(f"unknown habit {key}")
            num, txt = _validate(habits[key], val)
            if num is None and txt is None:
                cur = conn.execute("DELETE FROM journal_entries WHERE local_date=? AND habit_key=?", (d.isoformat(), key))
                if cur.rowcount:  # remember the deletion so the other device removes it too
                    conn.execute("INSERT INTO journal_tombstones(local_date, habit_key, deleted_at) VALUES (?,?,?) ON"
                                 " CONFLICT(local_date, habit_key) DO UPDATE SET deleted_at=excluded.deleted_at",
                                 (d.isoformat(), key, now))
                continue
            conn.execute("INSERT INTO journal_entries(local_date, habit_key, value_num, value_text, updated_at)"
                         " VALUES (?,?,?,?,?) ON CONFLICT(local_date, habit_key) DO UPDATE SET"
                         " value_num=excluded.value_num, value_text=excluded.value_text, updated_at=excluded.updated_at",
                         (d.isoformat(), key, num, txt, now))
            conn.execute("DELETE FROM journal_tombstones WHERE local_date=? AND habit_key=?", (d.isoformat(), key))
        if note is not None or tags is not None:
            conn.execute("INSERT INTO day_notes(local_date, note, tags, updated_at) VALUES (?,?,?,?) ON CONFLICT"
                         "(local_date) DO UPDATE SET note=excluded.note, tags=excluded.tags, updated_at=excluded.updated_at",
                         (d.isoformat(), note, json.dumps(tags or []), now))
    return get_day(db, d)


def entries_range(db: Database, start: date, end: date) -> list[dict]:
    return db.query("SELECT local_date, habit_key, value_num, value_text FROM journal_entries WHERE local_date BETWEEN"
                    " ? AND ? ORDER BY local_date", (start.isoformat(), end.isoformat()))


def private_habit_keys(db: Database) -> set[str]:
    return {r["key"] for r in db.query("SELECT key FROM journal_habits WHERE private=1")}
