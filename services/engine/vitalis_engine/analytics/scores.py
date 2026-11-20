"""Morning Recovery Estimate (recovery.v1) and cardio load (load.trimp-edwards.v1).

Original VITALIS framework — not a reproduction of any vendor score. The output is a *relative index*
(50 = your usual, each 20 points ≈ one robust SD of your own history) and never a medical percentage.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import numpy as np
import pandas as pd

from .. import ALGORITHM_VERSIONS
from ..db.connection import Database, utcnow
from ..timeutil import daterange
from .baseline import compute, settings
from .data import load_features

RECOVERY_VERSION = ALGORITHM_VERSIONS["recovery"]
LOAD_VERSION = ALGORITHM_VERSIONS["load"]

WEIGHTS = {"hrv": 0.35, "rhr": 0.25, "sleep": 0.25, "regularity": 0.075, "load": 0.075}
LIMITATIONS = [
    "Relative to your own recent history only; not comparable between people.",
    "Wrist-worn sensors estimate sleep and heart rate; values can be affected by fit, movement and illness.",
    "Not a medical assessment. It cannot detect or rule out disease.",
]


def category(z: float) -> str:
    if z >= 1.0:
        return "well_above_usual"
    if z >= 0.35:
        return "above_usual"
    if z > -0.35:
        return "typical"
    if z > -1.0:
        return "below_usual"
    return "well_below_usual"


def recovery_for_day(frame: pd.DataFrame, d: date, need_min: float, window: int, mp: int, ms: int) -> dict:
    used, missing, statuses = [], [], []

    def feat(name):
        if name not in frame.columns or d not in frame.index:
            return None
        v = frame.at[d, name]
        return None if pd.isna(v) else float(v)

    # Physiological components (personal baseline z-scores)
    for comp, fname, sign in (("hrv", "hrv_night_rmssd", 1.0), ("rhr", "rhr", -1.0)):
        v = feat(fname)
        if v is None:
            missing.append({"component": comp, "feature": fname, "reason": "no measurement for this night"})
            continue
        b = compute(frame[fname], fname, d, window, mp, ms)
        z = b.z(v)
        if z is None:
            missing.append({"component": comp, "feature": fname,
                            "reason": f"baseline {b.status} ({b.n} valid days; need {mp})"})
            continue
        statuses.append(b.status)
        c = float(np.clip(sign * z, -3, 3))
        used.append({"component": comp, "feature": fname, "value": v, "baseline_median": b.median,
                     "robust_sd": b.robust_sd, "baseline_n": b.n, "baseline_status": b.status, "z": z,
                     "score": c})
    tst = feat("sleep_tst_min")
    if tst is None:
        missing.append({"component": "sleep", "feature": "sleep_tst_min", "reason": "no main sleep recorded"})
    else:
        c = float(np.clip((tst - need_min) / 60.0, -2.5, 1.0))
        used.append({"component": "sleep", "feature": "sleep_tst_min", "value": tst, "reference": need_min,
                     "score": c, "note": "hours above/below your sleep need"})
    bed = feat("sleep_bedtime_min")
    if bed is not None:
        b = compute(frame["sleep_bedtime_min"], "sleep_bedtime_min", d, window, mp, ms)
        if b.median is not None and b.status != "insufficient":
            dev = abs(bed - b.median)
            c = float(np.clip(-max(0.0, dev - 30.0) / 45.0, -2.0, 0.0))
            used.append({"component": "regularity", "feature": "sleep_bedtime_min", "value": bed,
                         "baseline_median": b.median, "deviation_min": dev, "score": c})
        else:
            missing.append({"component": "regularity", "feature": "sleep_bedtime_min", "reason": "baseline insufficient"})
    else:
        missing.append({"component": "regularity", "feature": "sleep_bedtime_min", "reason": "no bedtime"})
    prev = d - timedelta(days=1)
    tr = float(frame.at[prev, "trimp"]) if "trimp" in frame.columns and prev in frame.index and not pd.isna(
        frame.at[prev, "trimp"]) else None
    if tr is not None:
        b = compute(frame["trimp"], "trimp", prev, window, mp, ms)
        z = b.z(tr)
        if z is not None:
            c = float(np.clip(-0.5 * max(0.0, z), -1.5, 0.0))
            used.append({"component": "load", "feature": "trimp", "value": tr, "date": prev.isoformat(),
                         "baseline_median": b.median, "z": z, "score": c})
        else:
            missing.append({"component": "load", "feature": "trimp", "reason": "baseline insufficient"})
    else:
        missing.append({"component": "load", "feature": "trimp", "reason": "no cardio-load data for previous day"})

    comps = {u["component"] for u in used}
    has_phys = bool(comps & {"hrv", "rhr"})
    detail = {"inputs_used": used, "inputs_missing": missing, "weights": WEIGHTS, "limitations": LIMITATIONS,
              "algorithm": RECOVERY_VERSION}
    if not (has_phys and "sleep" in comps):
        detail["reason"] = "Insufficient data: needs overnight HRV or resting HR with a baseline, plus sleep duration."
        return {"value": None, "z": None, "category": "insufficient_data", "completeness": 0.0,
                "baseline_status": "insufficient", "detail": detail}
    wsum = sum(WEIGHTS[u["component"]] for u in used)
    zc = sum(WEIGHTS[u["component"]] * u["score"] for u in used) / wsum
    for u in used:
        u["weight"] = WEIGHTS[u["component"]]
        u["contribution"] = WEIGHTS[u["component"]] * u["score"] / wsum
    status = "stable" if statuses and all(s == "stable" for s in statuses) else "provisional"
    return {"value": float(np.clip(50 + 20 * zc, 0, 100)), "z": float(zc), "category": category(zc),
            "completeness": float(wsum), "baseline_status": status, "detail": detail}


def compute_recovery(db: Database, start: date, end: date) -> int:
    window, mp, ms = settings(db)
    need = float(db.get_pref("sleep_need_min", 450))
    feats = ["hrv_night_rmssd", "rhr", "sleep_tst_min", "sleep_bedtime_min", "trimp"]
    frame = load_features(db, feats, start - timedelta(days=window + 1), end)
    rows, now = [], utcnow()
    for d in daterange(start, end):
        r = recovery_for_day(frame, d, need, window, mp, ms)
        rows.append((d.isoformat(), "recovery", r["value"], r["z"], r["category"], r["completeness"],
                     r["baseline_status"], RECOVERY_VERSION, json.dumps(r["detail"]), now))
    with db.transaction() as conn:
        conn.executemany("INSERT OR REPLACE INTO scores(local_date, kind, value, z, category, completeness,"
                         " baseline_status, algo_version, detail, computed_at) VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
        # Mirror as a daily feature so the index can be analysed like any other metric (Correlation Lab, trends).
        conn.execute("DELETE FROM daily_features WHERE feature='recovery_index' AND local_date BETWEEN ? AND ?",
                     (start.isoformat(), end.isoformat()))
        conn.executemany("INSERT OR REPLACE INTO daily_features(local_date, feature, value, algo_version, provenance,"
                         " detail, computed_at) VALUES (?,?,?,?,?,?,?)",
                         [(r[0], "recovery_index", r[2], RECOVERY_VERSION, "estimated", None, now) for r in rows
                          if r[2] is not None])
    return len(rows)


def compute_load(db: Database, start: date, end: date) -> int:
    """Acute (7-d) and chronic (28-d) exponentially weighted cardio load from daily TRIMP."""
    frame = load_features(db, ["trimp"], start - timedelta(days=60), end)
    s = frame["trimp"]
    if s.notna().sum() == 0:
        return 0
    filled = s.fillna(0.0)
    acute = filled.ewm(alpha=2 / (7 + 1), adjust=False).mean()
    chronic = filled.ewm(alpha=2 / (28 + 1), adjust=False).mean()
    observed = s.notna().rolling(28, min_periods=1).sum()
    rows, now = [], utcnow()
    for d in daterange(start, end):
        if d not in frame.index or observed.get(d, 0) < 14:
            continue
        a, c = float(acute[d]), float(chronic[d])
        ratio = a / c if c > 1e-6 else None
        rows.append((d.isoformat(), "load", a, ratio, None, float(observed[d]) / 28.0,
                     "stable" if observed[d] >= 24 else "provisional", LOAD_VERSION,
                     json.dumps({"acute_7d": a, "chronic_28d": c, "acute_chronic_ratio": ratio,
                                 "days_observed_28": int(observed[d]),
                                 "note": "Days without heart-rate data count as zero load; exploratory metric."}),
                     now))
    with db.transaction() as conn:
        conn.executemany("INSERT OR REPLACE INTO scores(local_date, kind, value, z, category, completeness,"
                         " baseline_status, algo_version, detail, computed_at) VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    return len(rows)


def get_scores(db: Database, kind: str, start: date, end: date) -> list[dict]:
    rows = db.query("SELECT * FROM scores WHERE kind=? AND local_date BETWEEN ? AND ? ORDER BY local_date",
                    (kind, start.isoformat(), end.isoformat()))
    for r in rows:
        r["detail"] = json.loads(r["detail"])
    return rows
