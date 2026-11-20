"""Sleep lab analytics: Sleep Regularity Index (sri.v1), sleep debt (sleepdebt.ewma.v1), planner, consistency."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from .. import ALGORITHM_VERSIONS
from ..db.connection import Database, utcnow
from ..model.canonical import ASLEEP_STAGES
from ..timeutil import DEFAULT_TZ, daterange, day_bounds_utc
from .data import load_features, load_sleep_episodes

SRI_VERSION = ALGORITHM_VERSIONS["sleep_regularity"]
DEBT_VERSION = ALGORITHM_VERSIONS["sleep_debt"]
DEBT_DECAY = 0.85
SRI_WINDOW = 14
SRI_MIN_PAIRS = 5


def _minute_states(db: Database, start: date, end: date, tz: str) -> dict[date, np.ndarray | None]:
    """1440-minute sleep(1)/wake(0) arrays per local day; None when the day has no sleep information."""
    eps = load_sleep_episodes(db, start - timedelta(days=1), end + timedelta(days=1), tz)
    days: dict[date, np.ndarray | None] = {}
    known = {e.wake_date for e in eps}
    for d in daterange(start, end):
        if d not in known and (d + timedelta(days=1)) not in known:
            days[d] = None
            continue
        a, b = day_bounds_utc(d, tz)
        n = int(round((b - a).total_seconds() / 60))
        arr = np.zeros(n, dtype=np.int8)
        for e in eps:
            segs = [(s0, s1) for st, s0, s1 in e.stages if st in ASLEEP_STAGES] if e.has_stages else [(e.start, e.end)]
            for s0, s1 in segs:
                lo = int(max(0, (s0 - a).total_seconds() // 60))
                hi = int(min(n, (s1 - a).total_seconds() // 60))
                if hi > lo:
                    arr[lo:hi] = 1
        # Normalize DST days (1380/1500 min) to 1440 by resampling on clock minutes.
        if n != 1440:
            arr = arr[np.linspace(0, n - 1, 1440).astype(int)]
        days[d] = arr
    return days


def sri_series(db: Database, start: date, end: date, window: int = SRI_WINDOW) -> dict[date, dict]:
    tz = db.get_pref("display_timezone", DEFAULT_TZ)
    states = _minute_states(db, start - timedelta(days=window), end, tz)
    out = {}
    for d in daterange(start, end):
        same, total, pairs = 0, 0, 0
        for k in range(window - 1):
            d1 = d - timedelta(days=k + 1)
            d0 = d - timedelta(days=k)
            a, b = states.get(d1), states.get(d0)
            if a is None or b is None:
                continue
            same += int((a == b).sum())
            total += len(a)
            pairs += 1
        if pairs >= SRI_MIN_PAIRS and total:
            out[d] = {"sri": -100.0 + 200.0 * same / total, "pairs": pairs}
    return out


def sleep_debt_series(db: Database, start: date, end: date) -> dict[date, dict]:
    need = float(db.get_pref("sleep_need_min", 450))
    frame = load_features(db, ["sleep_tst_min"], start - timedelta(days=30), end)
    debt, out = 0.0, {}
    for d, tst in frame["sleep_tst_min"].items():
        if pd.isna(tst):
            debt *= DEBT_DECAY
            observed = False
        else:
            debt = max(0.0, DEBT_DECAY * debt + (need - float(tst)))
            observed = True
        if d >= start:
            out[d] = {"debt_min": debt, "observed": observed, "need_min": need}
    return out


def store_sleep_extras(db: Database, start: date, end: date) -> int:
    now = utcnow()
    rows = []
    for d, v in sri_series(db, start, end).items():
        rows.append((d.isoformat(), "sleep_sri_14d", v["sri"], SRI_VERSION, json.dumps({"pairs": v["pairs"]}), now))
    for d, v in sleep_debt_series(db, start, end).items():
        rows.append((d.isoformat(), "sleep_debt_min", v["debt_min"], DEBT_VERSION,
                     json.dumps({"need_min": v["need_min"], "night_observed": v["observed"], "decay": DEBT_DECAY}), now))
    with db.transaction() as conn:
        conn.execute("DELETE FROM daily_features WHERE local_date BETWEEN ? AND ? AND feature IN"
                     " ('sleep_sri_14d','sleep_debt_min')", (start.isoformat(), end.isoformat()))
        conn.executemany("INSERT OR REPLACE INTO daily_features(local_date, feature, value, algo_version, detail,"
                         " computed_at) VALUES (?,?,?,?,?,?)", rows)
    return len(rows)


def _fmt_clock(minutes: float) -> str:
    m = int(round(minutes)) % 1440
    return f"{m // 60:02d}:{m % 60:02d}"


def consistency(db: Database, end: date, days: int = 30) -> dict:
    f = load_features(db, ["sleep_bedtime_min", "sleep_wake_min", "sleep_midpoint_min", "sleep_tst_min"],
                      end - timedelta(days=days - 1), end)
    out = {"window_days": days}
    for c in f.columns:
        s = f[c].dropna()
        out[c] = {"n": int(len(s)), "median": None if s.empty else float(s.median()),
                  "sd": None if len(s) < 3 else float(s.std(ddof=1))}
    idx = pd.to_datetime(pd.Series(f.index))
    wk = f[(idx.dt.weekday >= 5).to_numpy()]
    wd = f[(idx.dt.weekday < 5).to_numpy()]
    mp_we, mp_wd = wk["sleep_midpoint_min"].dropna(), wd["sleep_midpoint_min"].dropna()
    if len(mp_we) >= 2 and len(mp_wd) >= 3:
        out["social_jetlag_min"] = float(abs(mp_we.median() - mp_wd.median()))
    out["weekday"] = {c: (None if wd[c].dropna().empty else float(wd[c].median())) for c in f.columns}
    out["weekend"] = {c: (None if wk[c].dropna().empty else float(wk[c].median())) for c in f.columns}
    out["note"] = "Weekend = nights ending on Saturday or Sunday."
    return out


def planner(db: Database, wake_time: str, need_min: float | None = None, today: date | None = None) -> dict:
    today = today or date.today()
    need = float(need_min or db.get_pref("sleep_need_min", 450))
    hh, mm = wake_time.split(":")
    wake = int(hh) * 60 + int(mm)
    f = load_features(db, ["sleep_efficiency", "sleep_latency_min", "sleep_bedtime_min", "sleep_tst_min"],
                      today - timedelta(days=28), today)
    eff_s = f["sleep_efficiency"].dropna()
    eff = float(eff_s.median()) / 100.0 if len(eff_s) >= 5 else 0.90
    eff_src = f"your median over {len(eff_s)} nights" if len(eff_s) >= 5 else "default 90 % (not enough nights)"
    eff = float(np.clip(eff, 0.75, 0.98))
    tib = need / eff
    bed = wake - tib
    typical = f["sleep_bedtime_min"].dropna()
    typ = float(typical.median()) if len(typical) >= 5 else None
    shift_plan = []
    if typ is not None and abs(typ - bed) > 45:
        steps = int(np.ceil(abs(typ - bed) / 15.0))
        direction = -1 if bed < typ else 1
        for i in range(1, min(steps, 14) + 1):
            target = typ + direction * min(15 * i, abs(typ - bed))
            shift_plan.append({"day": (today + timedelta(days=2 * (i - 1))).isoformat(),
                               "bedtime": _fmt_clock(target)})
    return {"wake_time": wake_time, "need_min": need, "assumed_efficiency": round(eff, 3),
            "efficiency_source": eff_src, "time_in_bed_min": round(tib), "bedtime": _fmt_clock(bed),
            "window": [_fmt_clock(bed - 15), _fmt_clock(bed + 15)],
            "typical_bedtime": None if typ is None else _fmt_clock(typ), "gradual_shift": shift_plan,
            "assumptions": ["Time in bed = sleep need ÷ sleep efficiency.",
                            "Shift bedtime in 15-minute steps every two days when moving more than 45 minutes."]}


def save_plan(db: Database, plan: dict, start: date, routine: str | None = None) -> str:
    pid = str(uuid.uuid4())
    db.execute("UPDATE sleep_plans SET end_date=? WHERE end_date IS NULL", ((start - timedelta(days=1)).isoformat(),))
    db.execute("INSERT INTO sleep_plans(id, wake_time, bedtime_start, bedtime_end, need_min, start_date, routine,"
               " created_at) VALUES (?,?,?,?,?,?,?,?)",
               (pid, plan["wake_time"], plan["window"][0], plan["window"][1], plan["need_min"], start.isoformat(),
                routine, utcnow()))
    return pid


def _clock_to_rel(s: str) -> float:
    h, m = s.split(":")
    v = int(h) * 60 + int(m)
    return v - 1440 if v >= 12 * 60 else v  # evening times become negative minutes before midnight


def adherence(db: Database, until: date | None = None) -> dict:
    until = until or date.today()
    plan = db.query_one("SELECT * FROM sleep_plans ORDER BY start_date DESC LIMIT 1")
    if not plan:
        return {"plan": None}
    start = date.fromisoformat(plan["start_date"])
    f = load_features(db, ["sleep_bedtime_min", "sleep_wake_min"], start, until)
    lo, hi = _clock_to_rel(plan["bedtime_start"]), _clock_to_rel(plan["bedtime_end"])
    if hi < lo:
        hi += 1440
    wh, wm = plan["wake_time"].split(":")
    wake = int(wh) * 60 + int(wm)
    nights = []
    for d, r in f.iterrows():
        if pd.isna(r["sleep_bedtime_min"]):
            nights.append({"date": d.isoformat(), "status": "no_data"})
            continue
        ok_bed = lo - 15 <= r["sleep_bedtime_min"] <= hi + 15
        ok_wake = abs(r["sleep_wake_min"] - wake) <= 30
        nights.append({"date": d.isoformat(), "bed_ok": bool(ok_bed), "wake_ok": bool(ok_wake),
                       "status": "adherent" if ok_bed and ok_wake else "off_plan",
                       "bedtime": _fmt_clock(r["sleep_bedtime_min"]), "wake": _fmt_clock(r["sleep_wake_min"])})
    rated = [n for n in nights if n["status"] != "no_data"]
    return {"plan": plan, "nights": nights, "adherence_rate": (sum(n["status"] == "adherent" for n in rated) /
                                                                len(rated)) if rated else None,
            "nights_rated": len(rated)}
