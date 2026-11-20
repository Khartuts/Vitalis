"""Report assembly (all figures computed from stored data) and data export (CSV / JSON)."""

from __future__ import annotations

import csv
import json
import os
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from .. import ALGORITHM_VERSIONS, __version__
from ..analytics import insights, sleep, stats
from ..analytics.data import load_features
from ..analytics.features import feature_info
from ..analytics.scores import get_scores
from ..db.connection import Database, utcnow
from ..journal import list_habits
from ..quality.checks import coverage, source_reliability

SECTION_FEATURES = {
    "sleep": ["sleep_tst_min", "sleep_efficiency", "sleep_bedtime_min", "sleep_wake_min", "sleep_deep_min",
              "sleep_rem_min", "sleep_awakenings", "sleep_sri_14d", "sleep_debt_min", "sleep_score_imported"],
    "cardio": ["rhr", "hrv_night_rmssd", "hr_sleep_mean", "spo2_night_mean", "resp_rate_night", "bp_sys", "bp_dia"],
    "activity": ["steps", "active_kcal", "exercise_min", "trimp", "inactive_hours"],
    "stress": ["arousal_min", "lowmove_hr_median", "j:stress", "j:mood"],
}


def _summ(s: pd.Series) -> dict:
    v = s.dropna()
    if v.empty:
        return {"n": 0}
    return {"n": int(len(v)), "mean": float(v.mean()), "median": float(v.median()), "min": float(v.min()),
            "max": float(v.max()), "sd": float(v.std(ddof=1)) if len(v) > 1 else None}


def build(db: Database, start: date, end: date) -> dict:
    n_days = (end - start).days + 1
    prev_start, prev_end = start - timedelta(days=n_days), start - timedelta(days=1)
    feats = sorted({f for fs in SECTION_FEATURES.values() for f in fs})
    cur = load_features(db, feats, start, end)
    prev = load_features(db, feats, prev_start, prev_end)
    sections = {}
    for sec, fs in SECTION_FEATURES.items():
        rows = []
        for f in fs:
            c, p = _summ(cur[f]), _summ(prev[f])
            if c["n"] == 0 and p["n"] == 0:
                continue
            rows.append({"feature": f, "label": feature_info(f)["label"], "unit": feature_info(f)["unit"],
                         "current": c, "previous": p,
                         "series": [{"date": d.isoformat(), "value": None if pd.isna(v) else round(float(v), 2)}
                                    for d, v in cur[f].items()]})
        sections[sec] = rows
    rec = get_scores(db, "recovery", start, end)
    recovery = [{"date": r["local_date"], "value": r["value"], "z": r["z"], "category": r["category"]} for r in rec]
    cov = coverage(db, start, end)
    cov.pop("daily", None)
    habits = [h for h in list_habits(db) if h["active"] and h["value_type"] in ("boolean", "number", "scale", "time")]
    scan = stats.scan(db, [f"j:{h['key']}" for h in habits], ["sleep_tst_min", "sleep_efficiency", "rhr",
                                                              "hrv_night_rmssd"], start, end) if habits else None
    habit_rows = []
    if habits:
        j = load_features(db, [f"j:{h['key']}" for h in habits], start, end)
        for h in habits:
            s = j[f"j:{h['key']}"]
            habit_rows.append({"key": h["key"], "label": h["label"], "days_logged": int(s.notna().sum()),
                               "mean": None if s.dropna().empty else float(s.mean())})
    ins = insights.list_insights(db, since=start)
    recs = []
    for i in ins:
        if i.get("action"):
            recs.append({"title": i["title"], "action": i["action"]})
    demo = bool(db.get_pref("is_demo", False))
    return {
        "title": "VITALIS Personal Health Report", "generated_at": utcnow(), "app_version": __version__,
        "period": {"start": start.isoformat(), "end": end.isoformat(), "days": n_days},
        "comparison_period": {"start": prev_start.isoformat(), "end": prev_end.isoformat()},
        "demo": demo, "sections": sections, "recovery": recovery,
        "sleep_consistency": sleep.consistency(db, end, n_days),
        "coverage": cov, "sources": source_reliability(db)[:30], "habits": habit_rows,
        "correlations": None if scan is None else {**scan, "results": scan["results"][:15]},
        "important_changes": [i for i in ins if i["category"] != "habits"][:10],
        "recommendations": recs[:8],
        "methods": {"algorithms": ALGORITHM_VERSIONS,
                    "notes": ["Baselines: rolling median and scaled MAD over the previous 60 days (provisional ≥14 "
                              "valid days, stable ≥28).",
                              "Recovery: weighted personal z-scores of HRV, resting HR, sleep vs need, bedtime "
                              "regularity and prior-day load; a relative index, not a medical measure.",
                              "Correlations: Spearman with Bonett–Wright CI and autocorrelation-adjusted effective "
                              "N; screens use Benjamini–Hochberg FDR.",
                              "Sleep is dated by wake-up day. Wearable sleep stages are estimates."]},
        "disclaimer": ("This report summarizes wearable and self-reported data for personal wellness. It is not a "
                       "medical diagnosis and is not a substitute for professional advice."
                       + (" THIS REPORT USES SYNTHETIC DEMO DATA." if demo else "")),
    }


def export_all(db: Database, out_dir: str) -> dict:
    """One-click export: normalized measurements, sleep, exercise, journal, features and scores (CSV + JSON)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    files = {}
    queries = {
        "measurements": "SELECT m.record_id, m.metric, m.value_num, m.value_text, m.unit, m.orig_value, m.orig_unit,"
                        " m.start_utc, m.end_utc, m.utc_offset_min, m.local_date, m.provenance, m.quality_flags,"
                        " m.provider, m.provider_record_id, s.app_package, s.device_model, m.import_method,"
                        " m.imported_at, m.superseded_by FROM measurements m LEFT JOIN sources s ON s.id=m.source_id"
                        " WHERE m.deleted_at IS NULL ORDER BY m.start_utc",
        "sleep_sessions": "SELECT record_id, provider, provider_record_id, start_utc, end_utc, utc_offset_min,"
                          " local_date, has_stages, provenance, superseded_by FROM sleep_sessions WHERE deleted_at IS"
                          " NULL ORDER BY start_utc",
        "sleep_stages": "SELECT session_record_id, stage, start_utc, end_utc, orig_code FROM sleep_stages ORDER BY"
                        " start_utc",
        "exercise_sessions": "SELECT record_id, provider, exercise_type, start_utc, end_utc, local_date, distance_m,"
                             " active_kcal, avg_hr, max_hr, provenance FROM exercise_sessions WHERE deleted_at IS NULL",
        "journal_entries": "SELECT local_date, habit_key, value_num, value_text, provenance, updated_at FROM"
                           " journal_entries ORDER BY local_date",
        "daily_features": "SELECT local_date, feature, value, algo_version, provenance FROM daily_features ORDER BY"
                          " local_date, feature",
        "scores": "SELECT local_date, kind, value, z, category, completeness, baseline_status, algo_version FROM"
                  " scores ORDER BY local_date",
    }
    for name, q in queries.items():
        rows = db.query(q)
        p = out / f"{name}.csv"
        with open(p, "w", newline="", encoding="utf-8") as fh:
            if rows:
                w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
                w.writeheader()
                w.writerows(rows)
        files[name] = {"path": str(p), "rows": len(rows)}
    manifest = {"exported_at": utcnow(), "app_version": __version__, "algorithms": ALGORITHM_VERSIONS,
                "demo": bool(db.get_pref("is_demo", False)), "files": files,
                "habits": list_habits(db)}
    with open(out / "manifest.json", "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, default=str)
    return manifest


def export_features_csv(db: Database, path: str, start: date, end: date, features: list[str] | None = None) -> dict:
    f = load_features(db, features, start, end)
    f.index.name = "date"
    tmp = path + ".tmp"
    f.to_csv(tmp)
    os.replace(tmp, path)
    return {"path": path, "rows": int(len(f)), "columns": list(f.columns)}
