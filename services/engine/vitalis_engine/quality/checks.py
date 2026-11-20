"""Data-quality analysis: outliers, sensor gaps, coverage, freshness, source reliability."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

from ..db.connection import Database
from ..model.metrics import QualityFlag
from ..timeutil import DEFAULT_TZ, daterange

HR_SPIKE_BPM = 40.0
GAP_HOURS = 3.0


def flag_outliers(db: Database, start: date, end: date) -> dict:
    """Hampel-style filter for heart rate: |x − rolling median(9)| > 40 bpm outside exercise → OUTLIER flag."""
    rows = db.query("SELECT record_id, start_utc, value_num, quality_flags FROM measurements WHERE metric='heart_rate'"
                    " AND local_date BETWEEN ? AND ? AND deleted_at IS NULL ORDER BY start_utc",
                    (start.isoformat(), end.isoformat()))
    if len(rows) < 9:
        return {"heart_rate": 0}
    df = pd.DataFrame(rows)
    df["t"] = pd.to_datetime(df["start_utc"], utc=True, format="ISO8601")
    med = df["value_num"].rolling(9, center=True, min_periods=5).median()
    dev = (df["value_num"] - med).abs()
    ex = db.query("SELECT start_utc, end_utc FROM exercise_sessions WHERE local_date BETWEEN ? AND ?"
                  " AND deleted_at IS NULL", (start.isoformat(), end.isoformat()))
    in_ex = np.zeros(len(df), bool)
    for e in ex:
        a = pd.Timestamp(e["start_utc"]) - pd.Timedelta(minutes=10)
        b = pd.Timestamp(e["end_utc"]) + pd.Timedelta(minutes=10)
        in_ex |= ((df["t"] >= a) & (df["t"] <= b)).to_numpy()
    out = (dev > HR_SPIKE_BPM).to_numpy() & ~in_ex
    flag = int(QualityFlag.OUTLIER)
    with db.transaction() as conn:
        conn.executemany("UPDATE measurements SET quality_flags = quality_flags & ? WHERE record_id=?",
                         [(~flag & 0xFFFF, r) for r in df.loc[~out & ((df["quality_flags"] & flag) > 0), "record_id"]])
        conn.executemany("UPDATE measurements SET quality_flags = quality_flags | ? WHERE record_id=?",
                         [(flag, r) for r in df.loc[out, "record_id"]])
    return {"heart_rate": int(out.sum())}


def sensor_gaps(db: Database, d: date) -> list[dict]:
    rows = db.query("SELECT start_utc FROM measurements WHERE metric='heart_rate' AND local_date=? AND deleted_at IS"
                    " NULL AND superseded_by IS NULL ORDER BY start_utc", (d.isoformat(),))
    ts = [datetime.fromisoformat(r["start_utc"].replace("Z", "+00:00")) for r in rows]
    gaps = []
    for a, b in zip(ts, ts[1:]):
        h = (b - a).total_seconds() / 3600
        if h >= GAP_HOURS:
            gaps.append({"from": a.isoformat(), "to": b.isoformat(), "hours": round(h, 2)})
    return gaps


def coverage(db: Database, start: date, end: date) -> dict:
    feats = db.query("SELECT local_date, feature, value FROM daily_features WHERE local_date BETWEEN ? AND ? AND"
                     " feature IN ('sleep_tst_min','steps','hr_coverage_pct','hrv_night_rmssd','rhr','spo2_night_mean',"
                     "'resp_rate_night','skin_temp_delta_night','skin_temp_night')",
                     (start.isoformat(), end.isoformat()))
    days = [d.isoformat() for d in daterange(start, end)]
    by = {}
    for r in feats:
        by.setdefault(r["feature"], {})[r["local_date"]] = r["value"]
    per_metric = {}
    for f in ("sleep_tst_min", "steps", "hr_coverage_pct", "rhr", "hrv_night_rmssd", "spo2_night_mean",
              "resp_rate_night", "skin_temp_delta_night", "skin_temp_night"):
        present = by.get(f, {})
        per_metric[f] = {"days_present": len(present), "days_total": len(days),
                         "fraction": len(present) / len(days) if days else 0.0,
                         "missing_dates": [d for d in days if d not in present][-60:]}
    hrcov = by.get("hr_coverage_pct", {})
    daily = [{"date": d, "sleep": d in by.get("sleep_tst_min", {}), "steps": d in by.get("steps", {}),
              "hr_coverage_pct": hrcov.get(d), "hrv": d in by.get("hrv_night_rmssd", {})} for d in days]
    score = np.mean([(1 if x["sleep"] else 0) * 0.4 + (1 if x["steps"] else 0) * 0.2 +
                     min(1.0, (x["hr_coverage_pct"] or 0) / 80.0) * 0.4 for x in daily]) if daily else 0.0
    return {"per_metric": per_metric, "daily": daily, "completeness_score": float(score)}


def freshness(db: Database) -> list[dict]:
    rows = db.query("SELECT provider, metric, max(start_utc) AS newest, count(*) AS n FROM measurements WHERE"
                    " deleted_at IS NULL GROUP BY provider, metric ORDER BY provider, metric")
    now = datetime.now(timezone.utc)
    for r in rows:
        t = datetime.fromisoformat(r["newest"].replace("Z", "+00:00"))
        r["age_hours"] = round((now - t).total_seconds() / 3600, 1)
        r["stale"] = r["age_hours"] > 48
    sl = db.query("SELECT provider, max(end_utc) AS newest, count(*) AS n FROM sleep_sessions WHERE deleted_at IS NULL"
                  " GROUP BY provider")
    for r in sl:
        t = datetime.fromisoformat(r["newest"].replace("Z", "+00:00"))
        rows.append({"provider": r["provider"], "metric": "sleep_session", "newest": r["newest"], "n": r["n"],
                     "age_hours": round((now - t).total_seconds() / 3600, 1),
                     "stale": (now - t).total_seconds() > 48 * 3600})
    return rows


def source_reliability(db: Database) -> list[dict]:
    rows = db.query(
        "SELECT s.provider, s.app_package, s.device_type, s.device_manufacturer, s.device_model, m.metric,"
        " count(*) AS n, sum(CASE WHEN m.quality_flags & ? THEN 1 ELSE 0 END) AS flagged,"
        " sum(CASE WHEN m.superseded_by IS NOT NULL THEN 1 ELSE 0 END) AS superseded,"
        " min(m.start_utc) AS first, max(m.start_utc) AS last FROM measurements m JOIN sources s ON s.id=m.source_id"
        " WHERE m.deleted_at IS NULL GROUP BY s.id, m.metric ORDER BY n DESC",
        (int(QualityFlag.OUT_OF_RANGE | QualityFlag.OUTLIER | QualityFlag.FUTURE_TIMESTAMP),))
    for r in rows:
        r["flagged_fraction"] = r["flagged"] / r["n"] if r["n"] else 0.0
        r["reliability"] = "good" if r["flagged_fraction"] < 0.01 else "fair" if r["flagged_fraction"] < 0.05 else "poor"
    return rows


def quality_report(db: Database, start: date, end: date) -> dict:
    flags = db.query_one(
        "SELECT sum(CASE WHEN quality_flags & 1 THEN 1 ELSE 0 END) AS out_of_range,"
        " sum(CASE WHEN quality_flags & 2 THEN 1 ELSE 0 END) AS outliers,"
        " sum(CASE WHEN quality_flags & 16 THEN 1 ELSE 0 END) AS tz_inferred,"
        " sum(CASE WHEN quality_flags & 64 THEN 1 ELSE 0 END) AS future,"
        " sum(CASE WHEN superseded_by IS NOT NULL THEN 1 ELSE 0 END) AS duplicates_superseded, count(*) AS total"
        " FROM measurements WHERE local_date BETWEEN ? AND ? AND deleted_at IS NULL",
        (start.isoformat(), end.isoformat()))
    gaps = {d.isoformat(): g for d in daterange(max(start, end - timedelta(days=13)), end) if (g := sensor_gaps(db, d))}
    return {"period": [start.isoformat(), end.isoformat()], "coverage": coverage(db, start, end),
            "flags": flags, "sensor_gaps_last_14d": gaps, "freshness": freshness(db),
            "sources": source_reliability(db),
            "last_imports": db.query("SELECT id, provider, import_method, started_at, finished_at, status,"
                                     " records_received, records_inserted, records_updated, records_duplicate,"
                                     " records_rejected FROM import_batches ORDER BY started_at DESC LIMIT 20")}
