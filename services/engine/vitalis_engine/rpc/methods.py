"""JSON-RPC method implementations (desktop ↔ engine). All parameters are validated by Pydantic models."""

from __future__ import annotations

import json
import threading
import uuid
from datetime import date, timedelta
from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from .. import ALGORITHM_VERSIONS, __version__
from ..ai import prompt as ai_prompt
from ..ai import tools as ai_tools
from ..analytics import experiments, insights, intraday, sleep, stats, thermal
from ..analytics.baseline import baseline_for, settings as baseline_settings
from ..analytics.data import load_exercises, load_features, load_measurements, load_sleep_episodes
from ..analytics.features import FEATURE_INFO, feature_info
from ..analytics.recompute import data_date_range, recompute
from ..analytics.scores import get_scores
from ..db.backup import create_backup, restore_backup
from ..db.connection import utcnow
from ..ingest import google_health, samsung_export
from ..ingest.pipeline import DEFAULT_SOURCE_PRIORITY, Importer, source_priority
from ..journal import (delete_habit, entries_range, get_day, list_habits, private_habit_keys, set_day,
                       upsert_habit)
from ..quality.checks import quality_report
from ..reports import report as reports
from ..sync import crypto as sync_crypto
from ..sync.ingest import import_sync_payload
from .server import Engine, RpcError, method

ISO = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class P(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RangeP(P):
    start: str = ISO
    end: str = ISO

    def d(self) -> tuple[date, date]:
        a, b = date.fromisoformat(self.start), date.fromisoformat(self.end)
        if b < a or (b - a).days > 3700:
            raise RpcError("invalid_range", "invalid date range")
        return a, b


class DayP(P):
    date: str = ISO

    def d(self) -> date:
        return date.fromisoformat(self.date)


def _today(engine: Engine) -> date:
    rng = data_date_range(engine.db)
    real_today = date.today()
    if engine.demo and rng:
        return rng[1]
    return real_today


def _after_data_change(engine: Engine, start: str | None, end: str | None, reason: str) -> dict:
    with engine._recompute_lock:
        engine.event({"type": "progress", "stage": "recompute", "reason": reason})
        res = recompute(engine.db, date.fromisoformat(start) if start else None,
                        date.fromisoformat(end) if end else None, today=_today(engine),
                        progress=lambda s: engine.event({"type": "progress", "stage": s}))
    engine.event({"type": "data_changed", "reason": reason})
    return res


SYNC_RECOMPUTE_DEBOUNCE_S = 2.0
_sync_pending_lock = threading.Lock()


def _schedule_sync_recompute(engine: Engine, start: str | None, end: str | None) -> None:
    """A phone sends history in many batches; recompute once for their combined date range after they stop arriving."""
    with _sync_pending_lock:
        p = engine.__dict__.setdefault("_sync_pending", {"full": False, "start": None, "end": None, "timer": None})
        if start is None or end is None:
            p["full"] = True
        else:
            p["start"] = start if p["start"] is None else min(p["start"], start)
            p["end"] = end if p["end"] is None else max(p["end"], end)
        if p["timer"] is not None:
            p["timer"].cancel()
        p["timer"] = threading.Timer(SYNC_RECOMPUTE_DEBOUNCE_S, _flush_sync_recompute, args=(engine,))
        p["timer"].daemon = True
        p["timer"].start()


def _flush_sync_recompute(engine: Engine) -> None:
    with _sync_pending_lock:
        p = engine.__dict__["_sync_pending"]
        start, end = (None, None) if p["full"] else (p["start"], p["end"])
        p.update(full=False, start=None, end=None, timer=None)
    _after_data_change(engine, start, end, "sync")


def on_sync_event(engine: Engine, ev: dict) -> None:
    engine.event({"type": "sync", **{k: v for k, v in ev.items() if k != "summary"},
                  "summary": ev.get("summary")})
    if ev.get("event") == "batch_imported":
        s = ev.get("summary") or {}
        if s.get("inserted") or s.get("updated") or s.get("deleted"):
            _schedule_sync_recompute(engine, s.get("min_date"), s.get("max_date"))


# ---------------------------------------------------------------------------------------------- system / prefs
@method("system.status")
def system_status(engine: Engine):
    db = engine.db
    rng = data_date_range(db)
    counts = db.query_one("SELECT (SELECT count(*) FROM measurements WHERE deleted_at IS NULL) AS measurements,"
                          " (SELECT count(*) FROM sleep_sessions WHERE deleted_at IS NULL) AS sleep_sessions,"
                          " (SELECT count(*) FROM exercise_sessions WHERE deleted_at IS NULL) AS exercise_sessions,"
                          " (SELECT count(*) FROM journal_entries) AS journal_entries,"
                          " (SELECT max(finished_at) FROM import_batches WHERE status IN ('completed','partial'))"
                          " AS last_import")
    return {"version": __version__, "schema_version": db.schema_version(), "demo": engine.demo,
            "profile_name": db.get_pref("profile_name"), "data_range": [rng[0].isoformat(), rng[1].isoformat()]
            if rng else None, "today": _today(engine).isoformat(), "counts": counts,
            "algorithms": ALGORITHM_VERSIONS, "sync": engine.sync.status() if engine.sync else None}


PREF_KEYS = {"display_timezone", "sleep_need_min", "birth_year", "hr_max", "baseline_window_days",
             "baseline_min_provisional", "baseline_min_stable", "source_priority", "locale", "profile_name",
             "units", "onboarding_done", "dashboard_layout", "saved_views", "thermal_min_c", "thermal_max_c"}


@method("prefs.get")
def prefs_get(engine: Engine):
    out = {k: engine.db.get_pref(k) for k in PREF_KEYS}
    out["source_priority"] = source_priority(engine.db)
    out.setdefault("sleep_need_min", 450)
    if out["sleep_need_min"] is None:
        out["sleep_need_min"] = 450
    w, mp, ms = baseline_settings(engine.db)
    out.update(baseline_window_days=w, baseline_min_provisional=mp, baseline_min_stable=ms)
    out["thermal_min_c"] = float(out["thermal_min_c"] if out["thermal_min_c"] is not None else 16.0)
    out["thermal_max_c"] = float(out["thermal_max_c"] if out["thermal_max_c"] is not None else 22.0)
    return out


class PrefsSetP(P):
    values: dict[str, Any]


@method("prefs.set", PrefsSetP)
def prefs_set(engine: Engine, p: PrefsSetP):
    recompute_needed = False
    for k, v in p.values.items():
        if k not in PREF_KEYS:
            raise RpcError("invalid_pref", f"unknown preference {k}")
        if k == "display_timezone":
            from zoneinfo import ZoneInfo
            ZoneInfo(str(v))
        if k == "sleep_need_min" and not (240 <= float(v) <= 720):
            raise RpcError("invalid_pref", "sleep need must be 4–12 h")
        if k == "birth_year" and v is not None and not (1900 <= int(v) <= date.today().year - 10):
            raise RpcError("invalid_pref", "invalid birth year")
        if k == "hr_max" and v is not None and not (120 <= int(v) <= 230):
            raise RpcError("invalid_pref", "max HR must be 120–230")
        if k in ("thermal_min_c", "thermal_max_c"):
            v = float(v)
            if not (10.0 <= v <= 30.0):
                raise RpcError("invalid_pref", "bedroom temperature limits must be 10–30 °C")
        if k == "source_priority":
            if not isinstance(v, list) or not set(v) <= set(DEFAULT_SOURCE_PRIORITY):
                raise RpcError("invalid_pref", "invalid source priority")
        if k in ("baseline_window_days", "baseline_min_provisional", "baseline_min_stable"):
            v = int(v)
            if not (7 <= v <= 365):
                raise RpcError("invalid_pref", "baseline setting out of range")
        engine.db.set_pref(k, v)
        recompute_needed |= k in ("sleep_need_min", "birth_year", "hr_max", "source_priority", "display_timezone",
                                  "baseline_window_days", "baseline_min_provisional", "baseline_min_stable")
    if recompute_needed and data_date_range(engine.db):
        threading.Thread(target=_after_data_change, args=(engine, None, None, "preferences"), daemon=True).start()
    return prefs_get(engine)


# ---------------------------------------------------------------------------------------------- features
@method("features.catalog")
def features_catalog(engine: Engine):
    rows = {r["feature"]: r for r in engine.db.query(
        "SELECT feature, count(*) AS n, min(local_date) AS first, max(local_date) AS last FROM daily_features"
        " GROUP BY feature")}
    habits = {f"j:{h['key']}": h for h in list_habits(engine.db)}
    out = []
    for k in list(FEATURE_INFO) + list(habits):
        info = feature_info(k) if k not in habits else {"label": habits[k]["label"], "unit": habits[k]["unit"] or "",
                                                         "group": "journal", "provenance": "user_entered"}
        r = rows.get(k)
        out.append({"key": k, **info, "days": r["n"] if r else 0, "first": r["first"] if r else None,
                    "last": r["last"] if r else None, "private": bool(habits[k]["private"]) if k in habits else False})
    return out


class SeriesP(RangeP):
    features: list[str] = Field(min_length=1, max_length=40)


@method("features.series", SeriesP)
def features_series(engine: Engine, p: SeriesP):
    a, b = p.d()
    f = load_features(engine.db, p.features, a, b)
    return {"dates": [d.isoformat() for d in f.index],
            "series": {c: [None if pd.isna(v) else float(v) for v in f[c]] for c in f.columns},
            "info": {c: feature_info(c) for c in f.columns}}


@method("features.day", DayP)
def features_day(engine: Engine, p: DayP):
    rows = engine.db.query("SELECT feature, value, algo_version, provenance, detail FROM daily_features WHERE"
                           " local_date=?", (p.date,))
    for r in rows:
        r["detail"] = json.loads(r["detail"]) if r["detail"] else None
        r["info"] = feature_info(r["feature"])
    return rows


class BaselineP(DayP):
    feature: str


@method("baseline.get", BaselineP)
def baseline_get(engine: Engine, p: BaselineP):
    return baseline_for(engine.db, p.feature, p.d()).as_dict()


class ExplainDataP(DayP):
    features: list[str] = Field(default_factory=list, max_length=20)
    scores: list[Literal["recovery", "load", "energy_summary", "arousal"]] = Field(default_factory=list, max_length=4)


@method("explain.data", ExplainDataP)
def explain_data(engine: Engine, p: ExplainDataP):
    """'Your data' for an Explain panel: per metric, days with data (all / last 60 days) and the baseline status."""
    db, d = engine.db, p.d()
    since = (d - timedelta(days=59)).isoformat()
    window, min_prov, min_stable = baseline_settings(db)
    feats = []
    for f in p.features:
        if f not in FEATURE_INFO:
            raise RpcError("invalid_params", f"unknown feature: {f}")
        r = db.query_one("SELECT count(*) AS n, min(local_date) AS first, max(local_date) AS last,"
                         " sum(CASE WHEN local_date BETWEEN ? AND ? THEN 1 ELSE 0 END) AS recent"
                         " FROM daily_features WHERE feature=? AND value IS NOT NULL", (since, p.date, f))
        b = baseline_for(db, f, d)
        feats.append({"key": f, **feature_info(f), "days": r["n"], "recent_days": r["recent"] or 0,
                      "first": r["first"], "last": r["last"],
                      "baseline": {"status": b.status, "n": b.n, "median": b.median, "robust_sd": b.robust_sd}})
    scs = []
    for k in p.scores:
        r = db.query_one("SELECT count(*) AS n, min(local_date) AS first, max(local_date) AS last,"
                         " sum(CASE WHEN local_date BETWEEN ? AND ? THEN 1 ELSE 0 END) AS recent"
                         " FROM scores WHERE kind=? AND value IS NOT NULL", (since, p.date, k))
        cur = db.query_one("SELECT value, category, baseline_status FROM scores WHERE kind=? AND local_date=?", (k, p.date))
        scs.append({"kind": k, "days": r["n"], "recent_days": r["recent"] or 0, "first": r["first"], "last": r["last"],
                    "on_date": cur})
    return {"date": p.date, "recent_window_days": 60, "features": feats, "scores": scs,
            "baseline_settings": {"window_days": window, "min_provisional": min_prov, "min_stable": min_stable}}


@method("thermal.summary", RangeP)
def thermal_summary(engine: Engine, p: RangeP):
    a, b = p.d()
    return thermal.summary(engine.db, a, b)


@method("thermal.suggestion", DayP)
def thermal_suggestion(engine: Engine, p: DayP):
    """Suggested bedroom temperature for the night starting on the evening of `date`."""
    return thermal.suggestion(engine.db, p.d())


# ---------------------------------------------------------------------------------------------- overview
KEY_FEATURES = ["sleep_tst_min", "sleep_efficiency", "rhr", "hrv_night_rmssd", "steps", "arousal_min", "trimp",
                "spo2_night_mean", "resp_rate_night", "sleep_bedtime_min", "skin_temp_delta_night"]


@method("overview.get", DayP)
def overview_get(engine: Engine, p: DayP):
    db, d = engine.db, p.d()
    window, mp, ms = baseline_settings(db)
    f = load_features(db, KEY_FEATURES + ["sleep_deep_min", "sleep_rem_min", "active_kcal", "exercise_min",
                                          "sleep_score_imported", "energy_score_imported", "hr_coverage_pct",
                                          "sleep_debt_min", "sleep_sri_14d"], d - timedelta(days=window + 90), d)
    from ..analytics.baseline import compute
    comps = []
    for k in KEY_FEATURES:
        v = f.at[d, k] if d in f.index else np.nan
        b = compute(f[k], k, d, window, mp, ms)
        z = None if pd.isna(v) else b.z(float(v))
        comps.append({"feature": k, **feature_info(k), "value": None if pd.isna(v) else float(v),
                      "baseline": b.as_dict(), "z": z})
    better = {k: FEATURE_INFO.get(k, {}).get("better", "neutral") for k in KEY_FEATURES}
    notable = sorted([c for c in comps if c["z"] is not None], key=lambda c: -abs(c["z"]))
    most = notable[0] if notable and abs(notable[0]["z"]) >= 1.0 else None

    def period(n):
        cur = f.loc[d - timedelta(days=n - 1):d]
        prev = f.loc[d - timedelta(days=2 * n - 1):d - timedelta(days=n)]
        return {k: {"current": None if cur[k].dropna().empty else float(cur[k].mean()),
                    "previous": None if prev[k].dropna().empty else float(prev[k].mean()),
                    "n_current": int(cur[k].notna().sum()), "n_previous": int(prev[k].notna().sum())}
                for k in KEY_FEATURES}
    rec = get_scores(db, "recovery", d - timedelta(days=29), d)
    eps = load_sleep_episodes(db, d, d)
    ins = insights.list_insights(db, since=d - timedelta(days=14))
    actionable = [i for i in ins if i.get("action")][:3] or ins[:3]
    energy = intraday.energy_timeline(db, d)
    trend_feats = ["rhr", "hrv_night_rmssd", "arousal_min", "steps", "sleep_tst_min", "skin_temp_delta_night"]
    trends = {k: [{"date": dd.isoformat(), "value": None if pd.isna(v) else float(v)}
                  for dd, v in f.loc[d - timedelta(days=29):d, k].items()] for k in trend_feats}
    hrcov = f.at[d, "hr_coverage_pct"] if d in f.index else np.nan
    cov_days = f.loc[d - timedelta(days=6):d]
    dq = {"sleep_nights_7d": int(cov_days["sleep_tst_min"].notna().sum()),
          "hr_coverage_today": None if pd.isna(hrcov) else float(hrcov),
          "steps_days_7d": int(cov_days["steps"].notna().sum())}
    dq["score"] = (dq["sleep_nights_7d"] / 7 * 0.5 + dq["steps_days_7d"] / 7 * 0.2 +
                   min(1.0, (dq["hr_coverage_today"] or 0) / 80) * 0.3)
    return {"date": d.isoformat(), "demo": engine.demo, "comparisons": comps, "better": better,
            "most_important_change": most, "periods": {"1": period(1), "7": period(7), "30": period(30),
                                                       "90": period(90)},
            "recovery": rec[-1] if rec else None, "recovery_trend": [{"date": r["local_date"], "value": r["value"],
                                                                       "category": r["category"]} for r in rec],
            "last_sleep": eps[-1].to_dict() if eps else None,
            "sleep_today": {k: (None if pd.isna(f.at[d, k]) else float(f.at[d, k])) for k in
                            ("sleep_tst_min", "sleep_efficiency", "sleep_deep_min", "sleep_rem_min",
                             "sleep_score_imported", "sleep_debt_min", "sleep_sri_14d")} if d in f.index else {},
            "activity_today": {k: (None if pd.isna(f.at[d, k]) else float(f.at[d, k])) for k in
                               ("steps", "active_kcal", "exercise_min", "trimp")} if d in f.index else {},
            "energy": {k: energy.get(k) for k in ("status", "start_value", "end_value", "min_value", "missing_inputs",
                                                  "algorithm")} | {"points": [x for x in energy.get("points", [])
                                                                              if x.get("value") is not None][::4]},
            "trends": trends, "data_quality": dq, "insights": actionable,
            "energy_score_imported": None if d not in f.index or pd.isna(f.at[d, "energy_score_imported"])
            else float(f.at[d, "energy_score_imported"])}


# ---------------------------------------------------------------------------------------------- sleep
@method("sleep.nights", RangeP)
def sleep_nights(engine: Engine, p: RangeP):
    a, b = p.d()
    if (b - a).days > 400:
        raise RpcError("invalid_range", "max 400 days")
    eps = load_sleep_episodes(engine.db, a, b)
    cols = ["sleep_tst_min", "sleep_tib_min", "sleep_efficiency", "sleep_bedtime_min", "sleep_wake_min",
            "sleep_midpoint_min", "sleep_latency_min", "sleep_waso_min", "sleep_awakenings", "sleep_deep_min",
            "sleep_rem_min", "sleep_light_min", "sleep_awake_min", "sleep_deep_pct", "sleep_rem_pct", "sleep_light_pct", "nap_min",
            "sleep_score_imported", "hr_sleep_mean", "rhr", "hrv_night_rmssd", "spo2_night_mean", "spo2_night_min",
            "resp_rate_night", "skin_temp_delta_night", "skin_temp_night", "sleep_sri_14d", "sleep_debt_min"]
    f = load_features(engine.db, cols, a, b)
    rows = [{"date": d.isoformat(), **{c: (None if pd.isna(r[c]) else float(r[c])) for c in cols}}
            for d, r in f.iterrows()]
    return {"nights": rows, "episodes": [e.to_dict() for e in eps if (b - e.wake_date).days < 45],
            "info": {c: feature_info(c) for c in cols}, "need_min": engine.db.get_pref("sleep_need_min", 450),
            "debt_assumptions": {"need_min": engine.db.get_pref("sleep_need_min", 450), "decay_per_night": 0.85,
                                 "missing_nights": "debt decays without adding deficit"}}


class ConsistencyP(DayP):
    days: int = Field(30, ge=7, le=365)


@method("sleep.consistency", ConsistencyP)
def sleep_consistency(engine: Engine, p: ConsistencyP):
    return sleep.consistency(engine.db, p.d(), p.days)


class PlannerP(P):
    wake_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    need_min: float | None = Field(None, ge=240, le=720)


@method("sleep.planner", PlannerP)
def sleep_planner(engine: Engine, p: PlannerP):
    return sleep.planner(engine.db, p.wake_time, p.need_min, _today(engine))


class PlanSaveP(PlannerP):
    routine: str | None = Field(None, max_length=500)


@method("sleep.plan_save", PlanSaveP)
def sleep_plan_save(engine: Engine, p: PlanSaveP):
    plan = sleep.planner(engine.db, p.wake_time, p.need_min, _today(engine))
    pid = sleep.save_plan(engine.db, plan, _today(engine) + timedelta(days=1), p.routine)
    return {"id": pid, "plan": plan}


@method("sleep.adherence")
def sleep_adherence(engine: Engine):
    return sleep.adherence(engine.db, _today(engine))


# ---------------------------------------------------------------------------------------------- recovery / energy / stress
@method("recovery.range", RangeP)
def recovery_range(engine: Engine, p: RangeP):
    a, b = p.d()
    return get_scores(engine.db, "recovery", a, b)


@method("load.range", RangeP)
def load_range(engine: Engine, p: RangeP):
    a, b = p.d()
    return get_scores(engine.db, "load", a, b)


@method("energy.day", DayP)
def energy_day(engine: Engine, p: DayP):
    return intraday.energy_timeline(engine.db, p.d())


@method("stress.timeline", DayP)
def stress_timeline(engine: Engine, p: DayP):
    return intraday.arousal_timeline(engine.db, p.d())


@method("stress.summary", RangeP)
def stress_summary(engine: Engine, p: RangeP):
    a, b = p.d()
    feats = ["arousal_min", "arousal_mean_elev", "lowmove_hr_median", "rhr", "hrv_night_rmssd", "j:stress", "j:mood",
             "j:energy", "j:sleepiness", "lowmove_coverage_min"]
    f = load_features(engine.db, feats, a, b)
    idx = pd.to_datetime(pd.Series(f.index))
    wk = (idx.dt.weekday >= 5).to_numpy()
    by = lambda m: {c: (None if f.loc[m, c].dropna().empty else float(f.loc[m, c].mean())) for c in feats}
    daily = [{"date": d.isoformat(), **{c: (None if pd.isna(r[c]) else float(r[c])) for c in feats}}
             for d, r in f.iterrows()]
    # morning vs afternoon over the last 14 days with data (intraday)
    mor, aft, n = 0, 0, 0
    for d in [x for x in f.index[-14:]]:
        t = intraday.arousal_timeline(engine.db, d)
        if t["status"] == "insufficient_data":
            continue
        mor += t["summary"]["morning_elevated_min"]
        aft += t["summary"]["afternoon_elevated_min"]
        n += 1
    corr = stats.correlate(f["j:stress"], f["arousal_min"], "spearman")
    return {"daily": daily, "weekday": by(~wk), "weekend": by(wk),
            "morning_vs_afternoon": {"days": n, "morning_elevated_min_avg": mor / n if n else None,
                                     "afternoon_elevated_min_avg": aft / n if n else None},
            "subjective_vs_physiological": corr, "info": {c: feature_info(c) for c in feats},
            "note": "Physiological arousal = heart rate ≥12 bpm above your usual low-movement heart rate while "
                    "awake and not exercising. It is not a diagnosis of stress."}


# ---------------------------------------------------------------------------------------------- cardio / activity
@method("cardio.summary", RangeP)
def cardio_summary(engine: Engine, p: RangeP):
    a, b = p.d()
    feats = ["rhr", "rhr_reported", "rhr_sleep_est", "hrv_night_rmssd", "hr_sleep_mean", "hr_day_mean", "hr_day_max",
             "spo2_night_mean", "spo2_night_min", "resp_rate_night", "bp_sys", "bp_dia", "vo2max",
             "skin_temp_delta_night"]
    f = load_features(engine.db, feats, a, b)
    hr = load_measurements(engine.db, ["heart_rate"], max(a, b - timedelta(days=89)), b)
    hist = None
    if not hr.empty:
        counts, edges = np.histogram(hr["value"], bins=np.arange(30, 211, 5))
        hist = {"edges": edges.tolist(), "counts": counts.tolist(), "n": int(len(hr)),
                "period": [max(a, b - timedelta(days=89)).isoformat(), b.isoformat()]}
    events = engine.db.query("SELECT metric, value_text, start_utc, provider, meta FROM measurements WHERE metric IN"
                             " ('irregular_rhythm_event','sleep_apnea_event') AND local_date BETWEEN ? AND ? AND"
                             " deleted_at IS NULL ORDER BY start_utc", (a.isoformat(), b.isoformat()))
    bp = engine.db.query("SELECT start_utc, metric, value_num, provider FROM measurements WHERE metric IN"
                         " ('bp_systolic','bp_diastolic') AND local_date BETWEEN ? AND ? AND deleted_at IS NULL AND"
                         " superseded_by IS NULL ORDER BY start_utc", (a.isoformat(), b.isoformat()))
    rel = {}
    for x, y in (("sleep_tst_min", "rhr"), ("sleep_tst_min", "hrv_night_rmssd")):
        ff = load_features(engine.db, [x, y], a, b)
        rel[f"{x}~{y}"] = {**stats.correlate(ff[x], ff[y]),
                           "points": [{"x": float(r[x]), "y": float(r[y]), "date": d.isoformat()} for d, r in
                                      ff.dropna().iterrows()]}
    return {"daily": [{"date": d.isoformat(), **{c: (None if pd.isna(r[c]) else float(r[c])) for c in feats}}
                      for d, r in f.iterrows()], "hr_histogram": hist, "events": events, "blood_pressure": bp,
            "relationships": rel, "info": {c: feature_info(c) for c in feats},
            "notes": ["HRV values are imported from sources that measure beat-to-beat intervals; VITALIS never derives "
                      "HRV from averaged heart rate.", "Not a diagnostic tool; discuss concerns with a clinician."]}


@method("activity.summary", RangeP)
def activity_summary(engine: Engine, p: RangeP):
    a, b = p.d()
    feats = ["steps", "active_kcal", "distance_m", "floors", "exercise_min", "exercise_count", "inactive_hours",
             "hr_z1_min", "hr_z2_min", "hr_z3_min", "hr_z4_min", "hr_z5_min", "trimp", "sleep_tst_min"]
    f = load_features(engine.db, feats, a, b)
    ex = load_exercises(engine.db, a, b)
    sessions = [{"record_id": r.record_id, "date": r.local_date, "type": r.exercise_type, "title": r.title,
                 "start": r.start.isoformat(), "end": r.end.isoformat(),
                 "minutes": round((r.end - r.start).total_seconds() / 60, 1), "provider": r.provider,
                 "distance_m": None if pd.isna(r.distance_m) else r.distance_m,
                 "active_kcal": None if pd.isna(r.active_kcal) else r.active_kcal} for r in ex.itertuples()]
    tz = engine.db.get_pref("display_timezone", "Europe/Zagreb")
    st = load_measurements(engine.db, ["steps"], max(a, b - timedelta(days=55)), b)
    heat = []
    if not st.empty:
        st = st.assign(lt=st["start"].dt.tz_convert(tz))
        g = st.groupby([st["lt"].dt.date, st["lt"].dt.hour])["value"].sum()
        heat = [{"date": d.isoformat(), "hour": int(h), "steps": float(v)} for (d, h), v in g.items()]
    load = get_scores(engine.db, "load", a, b)
    idx = pd.to_datetime(pd.Series(f.index))
    weekly = f.assign(_w=idx.dt.to_period("W-SUN").astype(str).to_numpy()).groupby("_w").agg(
        {"steps": "mean", "exercise_min": "sum", "exercise_count": "sum", "trimp": "sum"})
    rest_days = int((f["exercise_count"].fillna(0) == 0).sum())
    return {"daily": [{"date": d.isoformat(), **{c: (None if pd.isna(r[c]) else float(r[c])) for c in feats}}
                      for d, r in f.iterrows()], "sessions": sessions, "steps_heatmap": heat,
            "load": [{"date": x["local_date"], **x["detail"]} for x in load],
            "weekly": [{"week": w, **{k: (None if pd.isna(v) else float(v)) for k, v in r.items()}}
                       for w, r in weekly.iterrows()], "rest_days": rest_days,
            "movement_consistency_cv": float(f["steps"].std() / f["steps"].mean()) if f["steps"].notna().sum() > 3
            and f["steps"].mean() else None, "info": {c: feature_info(c) for c in feats}}


# ---------------------------------------------------------------------------------------------- comparisons & stats
class CompareP(P):
    feature: str
    a_start: str = ISO
    a_end: str = ISO
    b_start: str = ISO
    b_end: str = ISO


@method("compare.periods", CompareP)
def compare_periods(engine: Engine, p: CompareP):
    r = ai_tools.call_tool(engine.db, "compare_periods", {"metric": p.feature, "a_start": p.a_start, "a_end": p.a_end,
                                                          "b_start": p.b_start, "b_end": p.b_end}, cloud=False)
    if not r["ok"]:
        raise RpcError(r["error"]["code"], r["error"]["message"])
    return r["result"]


class CorrP(RangeP):
    x: str
    y: str
    lag: int | None = Field(None, ge=0, le=7)
    method: Literal["spearman", "pearson"] = "spearman"
    rolling_window: int = Field(30, ge=10, le=120)


@method("stats.correlate", CorrP)
def stats_correlate(engine: Engine, p: CorrP):
    a, b = p.d()
    lag = stats.default_lag(p.y, p.x) if p.lag is None else p.lag
    f = load_features(engine.db, list(dict.fromkeys([p.x, p.y])), a, b + timedelta(days=lag))
    x = f[p.x].loc[:b]
    y = f[p.y].shift(-lag).loc[:b]
    res = stats.correlate(x, y, p.method)
    both = pd.concat([x.rename("x"), y.rename("y")], axis=1).dropna()
    res.update(lag_days=lag, x=p.x, y=p.y, x_info=feature_info(p.x), y_info=feature_info(p.y),
               points=[{"date": d.isoformat(), "x": float(r["x"]), "y": float(r["y"])} for d, r in both.iterrows()],
               rolling=stats.rolling_correlation(x, y, p.rolling_window),
               lagged=[{k: v for k, v in r.items() if k in ("lag_days", "r", "ci", "n", "verdict")}
                       for r in stats.lagged(engine.db, p.x, p.y, a, b, method=p.method)])
    for lab, key in ((p.x, "x"), (p.y, "y")):
        if lab.startswith("j:"):
            res[f"{key}_info"]["label"] = stats.habit_labels(engine.db).get(lab, lab)
    return res


class HabitP(RangeP):
    habit: str
    outcome: str
    condition: dict | None = None
    lag: int | None = Field(None, ge=0, le=7)
    adjust: list[str] = Field(default_factory=list, max_length=4)


@method("stats.habit", HabitP)
def stats_habit(engine: Engine, p: HabitP):
    a, b = p.d()
    return stats.habit_analysis(engine.db, p.habit, p.outcome, a, b, p.lag, p.condition, p.adjust or None)


class ScanP(RangeP):
    exposures: list[str] | None = None
    outcomes: list[str] = Field(default_factory=lambda: ["sleep_tst_min", "sleep_efficiency", "rhr",
                                                         "hrv_night_rmssd", "arousal_min"])


@method("stats.scan", ScanP)
def stats_scan(engine: Engine, p: ScanP):
    a, b = p.d()
    exp = p.exposures or [f"j:{h['key']}" for h in list_habits(engine.db, include_inactive=False)
                          if h["value_type"] in ("boolean", "number", "scale", "time")]
    return stats.scan(engine.db, exp, p.outcomes, a, b)


# ---------------------------------------------------------------------------------------------- journal
@method("journal.habits")
def journal_habits(engine: Engine):
    return list_habits(engine.db)


class HabitUpsertP(P):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,40}$")
    label: str = Field(min_length=1, max_length=80)
    category: str = Field("custom", max_length=40)
    value_type: Literal["boolean", "number", "time", "category", "scale", "text"]
    unit: str | None = Field(None, max_length=20)
    options: dict | None = None
    private: bool = False
    active: bool = True


@method("journal.habit_upsert", HabitUpsertP)
def journal_habit_upsert(engine: Engine, p: HabitUpsertP):
    return upsert_habit(engine.db, p.model_dump())


class KeyP(P):
    key: str = Field(max_length=64)


@method("journal.habit_delete", KeyP)
def journal_habit_delete(engine: Engine, p: KeyP):
    delete_habit(engine.db, p.key)
    return {"ok": True}


@method("journal.day_get", DayP)
def journal_day_get(engine: Engine, p: DayP):
    return get_day(engine.db, p.d())


class DaySetP(DayP):
    values: dict[str, Any]
    note: str | None = Field(None, max_length=5000)
    tags: list[str] | None = None


@method("journal.day_set", DaySetP)
def journal_day_set(engine: Engine, p: DaySetP):
    out = set_day(engine.db, p.d(), p.values, p.note, p.tags)
    threading.Thread(target=_after_data_change, args=(engine, p.date, p.date, "journal"), daemon=True).start()
    return out


@method("journal.range", RangeP)
def journal_range(engine: Engine, p: RangeP):
    a, b = p.d()
    return entries_range(engine.db, a, b)


# ---------------------------------------------------------------------------------------------- experiments
class ExperimentCreateP(P):
    title: str = Field(min_length=1, max_length=120)
    hypothesis: str = Field(min_length=1, max_length=500)
    habit_key: str
    condition: dict | None = None
    outcome: str
    lag_days: int = Field(1, ge=0, le=3)
    design: Literal["ab", "randomized_blocks"] = "randomized_blocks"
    start_date: str = ISO
    baseline_days: int = Field(7, ge=0, le=60)
    intervention_days: int = Field(28, ge=8, le=120)
    block_days: int = Field(2, ge=1, le=7)


@method("experiments.create", ExperimentCreateP)
def experiments_create(engine: Engine, p: ExperimentCreateP):
    return experiments.create(engine.db, p.model_dump())


@method("experiments.list")
def experiments_list(engine: Engine):
    return experiments.list_all(engine.db)


class IdP(P):
    id: str = Field(max_length=64)


@method("experiments.analyze", IdP)
def experiments_analyze(engine: Engine, p: IdP):
    return experiments.analyze(engine.db, p.id, _today(engine))


class StatusP(IdP):
    status: Literal["planned", "running", "completed", "cancelled"]


@method("experiments.status", StatusP)
def experiments_status(engine: Engine, p: StatusP):
    return experiments.update_status(engine.db, p.id, p.status)


@method("experiments.delete", IdP)
def experiments_delete(engine: Engine, p: IdP):
    experiments.delete(engine.db, p.id)
    return {"ok": True}


# ---------------------------------------------------------------------------------------------- insights & briefs
class SinceP(P):
    since: str | None = None
    include_dismissed: bool = False


@method("insights.list", SinceP)
def insights_list(engine: Engine, p: SinceP):
    return insights.list_insights(engine.db, date.fromisoformat(p.since) if p.since else None, p.include_dismissed)


@method("insights.dismiss", IdP)
def insights_dismiss(engine: Engine, p: IdP):
    engine.db.execute("UPDATE insights SET dismissed=1 WHERE id=?", (p.id,))
    return {"ok": True}


class BriefP(DayP):
    kind: Literal["morning", "weekly", "monthly", "quarterly"]


@method("briefs.get", BriefP)
def briefs_get(engine: Engine, p: BriefP):
    d = p.d()
    n = {"morning": 1, "weekly": 7, "monthly": 30, "quarterly": 90}[p.kind]
    feats = ["sleep_tst_min", "sleep_efficiency", "rhr", "hrv_night_rmssd", "steps", "exercise_min", "arousal_min",
             "sleep_sri_14d", "trimp"]
    f = load_features(engine.db, feats, d - timedelta(days=2 * n - 1 + (60 if n == 1 else 0)), d)
    cur = f.loc[d - timedelta(days=n - 1):d]
    prev = f.loc[d - timedelta(days=2 * n - 1):d - timedelta(days=n)]
    rows = []
    for c in feats:
        cv, pv = cur[c].dropna(), prev[c].dropna()
        row = {"feature": c, **feature_info(c), "current": None if cv.empty else float(cv.mean()),
               "previous": None if pv.empty else float(pv.mean()), "n_current": int(len(cv)),
               "n_previous": int(len(pv))}
        if n == 1:
            b = baseline_for(engine.db, c, d, f)
            row["baseline"] = b.as_dict()
            row["z"] = None if cv.empty else b.z(float(cv.iloc[-1]))
        rows.append(row)
    rec = get_scores(engine.db, "recovery", d - timedelta(days=n - 1), d)
    ins = insights.list_insights(engine.db, since=d - timedelta(days=max(n, 3)))
    return {"kind": p.kind, "date": d.isoformat(), "period": [(d - timedelta(days=n - 1)).isoformat(), d.isoformat()],
            "metrics": rows, "recovery": rec[-1] if p.kind == "morning" and rec else
            {"mean_index": float(np.nanmean([r["value"] for r in rec if r["value"] is not None]))
             if any(r["value"] is not None for r in rec) else None, "days": len(rec)},
            "insights": ins[:8], "demo": engine.demo}


# ---------------------------------------------------------------------------------------------- quality & sources
@method("quality.report", RangeP)
def quality_report_m(engine: Engine, p: RangeP):
    a, b = p.d()
    return quality_report(engine.db, a, b)


@method("sources.availability")
def sources_availability(engine: Engine):
    rows = engine.db.query("SELECT device_id, provider, received_at, report FROM availability_reports ORDER BY"
                           " received_at DESC LIMIT 10")
    for r in rows:
        r["report"] = json.loads(r["report"])
    return rows


# ---------------------------------------------------------------------------------------------- import
class ImportPathP(P):
    kind: Literal["samsung_export", "vitalis_sync_file"]
    path: str = Field(min_length=1, max_length=1024)


@method("import.preview", ImportPathP)
def import_preview(engine: Engine, p: ImportPathP):
    if p.kind == "samsung_export":
        return samsung_export.preview(p.path)
    blob = open(p.path, "rb").read()
    payload = json.loads(sync_crypto.decrypt_file_payload(sync_crypto.file_key(engine.db), blob))
    from collections import Counter
    return {"schema": payload.get("schema"), "batch_id": payload.get("batch_id"), "source": payload.get("source"),
            "device": payload.get("device"), "records": len(payload.get("records") or []),
            "types": dict(Counter(r.get("type") for r in payload.get("records") or [])),
            "already_imported": Importer(engine.db).batch_exists(str(payload.get("batch_id")))}


@method("import.commit", ImportPathP)
def import_commit(engine: Engine, p: ImportPathP):
    if p.kind == "samsung_export":
        s = Importer(engine.db).run(provider="samsung_export", import_method="file_import",
                                    records=samsung_export.iter_records(p.path), adapter=samsung_export.normalize,
                                    detail={"adapter": samsung_export.ADAPTER_VERSION},
                                    progress=lambda i: engine.event({"type": "progress", "stage": "import", "n": i}))
        summary = s.as_dict()
    else:
        blob = open(p.path, "rb").read()
        try:
            payload = json.loads(sync_crypto.decrypt_file_payload(sync_crypto.file_key(engine.db), blob))
        except Exception as exc:
            raise RpcError("decrypt_failed", "File could not be decrypted with this profile's pairing key "
                                             "(wrong profile, re-paired phone, or damaged file).") from exc
        summary = import_sync_payload(engine.db, payload, "file_sync")
    if summary.get("inserted") or summary.get("updated") or summary.get("deleted"):
        summary["recompute"] = _after_data_change(engine, summary.get("min_date"), summary.get("max_date"), "import")
    return summary


class GoogleImportP(P):
    records: list[dict] = Field(max_length=200_000)
    batch_id: str | None = None
    detail: dict | None = None


@method("import.google_health", GoogleImportP)
def import_google(engine: Engine, p: GoogleImportP):
    s = Importer(engine.db).run(provider="google_health", import_method="oauth_api", records=p.records,
                                adapter=google_health.normalize, batch_id=p.batch_id, detail=p.detail)
    summary = s.as_dict()
    if summary.get("inserted") or summary.get("updated"):
        summary["recompute"] = _after_data_change(engine, summary.get("min_date"), summary.get("max_date"), "google")
    return summary


@method("import.google_filter")
def import_google_filter(engine: Engine):
    return {"types": list(google_health.SUPPORTED_TYPES), "scopes": google_health.SCOPES_READONLY}


class GoogleFilterP(P):
    data_type: str
    start: str
    end: str


@method("import.google_filter_for", GoogleFilterP)
def import_google_filter_for(engine: Engine, p: GoogleFilterP):
    from ..timeutil import parse_instant
    if p.data_type not in google_health.SUPPORTED_TYPES:
        raise RpcError("unsupported", "unsupported data type")
    return {"filter": google_health.list_filter(p.data_type, parse_instant(p.start), parse_instant(p.end))}


@method("import.history")
def import_history(engine: Engine):
    rows = engine.db.query("SELECT * FROM import_batches ORDER BY started_at DESC LIMIT 100")
    for r in rows:
        r["detail"] = json.loads(r["detail"]) if r["detail"] else None
    return rows


# ---------------------------------------------------------------------------------------------- sync
class SyncStartP(P):
    port: int = Field(47821, ge=1024, le=65535)


def _lan_sync(engine: Engine):
    if engine.sync is None:
        raise RpcError("not_available", "The phone-sync server runs on the desktop app only.")
    return engine.sync


@method("sync.start", SyncStartP)
def sync_start(engine: Engine, p: SyncStartP):
    return _lan_sync(engine).start(p.port)


@method("sync.stop")
def sync_stop(engine: Engine):
    return _lan_sync(engine).stop()


@method("sync.status")
def sync_status(engine: Engine):
    if engine.sync is None:
        return {"running": False, "available": False, "port": None, "hosts": [], "fingerprint": None, "devices": []}
    return engine.sync.status()


class PairP(P):
    desktop_name: str = Field("VITALIS Desktop", max_length=40)


@method("sync.pairing", PairP)
def sync_pairing(engine: Engine, p: PairP):
    return _lan_sync(engine).pairing_payload(p.desktop_name)


@method("sync.revoke", IdP)
def sync_revoke(engine: Engine, p: IdP):
    engine.db.execute("UPDATE sync_devices SET revoked_at=? WHERE id=?", (utcnow(), p.id))
    return sync_status(engine)


@method("sync.rotate_file_key")
def sync_rotate(engine: Engine):
    sync_crypto.rotate_file_key(engine.db)
    return {"ok": True, "note": "Re-pair phones to use encrypted file transfer."}


class LocalBatchP(P):
    payload: dict[str, Any]


@method("ingest.local_batch", LocalBatchP)
def ingest_local_batch(engine: Engine, p: LocalBatchP):
    """Phone app only (not on the renderer allow-list): a vitalis.sync.v1 batch read from this phone's Health Connect,
    imported into the phone's own engine exactly as the desktop imports batches from the phone."""
    try:
        summary = import_sync_payload(engine.db, p.payload, "phone_local")
    except ValueError as exc:
        raise RpcError("invalid_batch", str(exc))
    if summary.get("inserted") or summary.get("updated") or summary.get("deleted"):
        _schedule_sync_recompute(engine, summary.get("min_date"), summary.get("max_date"))
    return summary


# ---------------------------------------------------------------------------------------------- export / backup / data
class DirP(P):
    path: str = Field(min_length=1, max_length=1024)


@method("export.all", DirP)
def export_all(engine: Engine, p: DirP):
    return reports.export_all(engine.db, p.path)


class FeatCsvP(RangeP):
    path: str = Field(min_length=1, max_length=1024)
    features: list[str] | None = None


@method("export.features_csv", FeatCsvP)
def export_features_csv(engine: Engine, p: FeatCsvP):
    a, b = p.d()
    return reports.export_features_csv(engine.db, p.path, a, b, p.features)


class BackupP(P):
    path: str = Field(min_length=1, max_length=1024)
    passphrase: str = Field(min_length=10, max_length=512)


@method("backup.create", BackupP)
def backup_create(engine: Engine, p: BackupP):
    return create_backup(engine.db, p.path, p.passphrase)


class RestoreP(BackupP):
    target_path: str = Field(min_length=1, max_length=1024)
    target_key: str = Field(pattern=r"^[0-9a-fA-F]{64}$")


@method("backup.restore_to", RestoreP)
def backup_restore(engine: Engine, p: RestoreP):
    return restore_backup(p.path, p.passphrase, p.target_path, p.target_key)


class WipeP(P):
    confirm: Literal["DELETE ALL MY DATA"]


@method("data.wipe", WipeP)
def data_wipe(engine: Engine, p: WipeP):
    tables = ["sleep_stages", "sleep_sessions", "exercise_sessions", "measurements", "raw_records", "import_batches",
              "daily_features", "scores", "insights", "journal_entries", "day_notes", "experiments", "sleep_plans",
              "chat_messages", "chat_threads", "ai_disclosures", "availability_reports", "sources"]
    with engine.db.transaction() as conn:
        for t in tables:
            conn.execute(f"DELETE FROM {t}")
    engine.db.execute("VACUUM")
    engine.event({"type": "data_changed", "reason": "wipe"})
    return {"ok": True, "tables_cleared": tables}


class DemoP(P):
    days: int = Field(90, ge=30, le=730)


@method("demo.load", DemoP)
def demo_load(engine: Engine, p: DemoP):
    from .. import synthetic
    res = synthetic.load_into(engine.db, p.days, date.today() - timedelta(days=1))
    res["recompute"] = _after_data_change(engine, None, None, "demo")
    return res


@method("recompute.run")
def recompute_run(engine: Engine):
    return _after_data_change(engine, None, None, "manual")


# ---------------------------------------------------------------------------------------------- AI
class AiToolsP(P):
    cloud: bool


@method("ai.tools", AiToolsP)
def ai_tools_defs(engine: Engine, p: AiToolsP):
    return ai_tools.tool_definitions(engine.db, p.cloud)


class AiCallP(P):
    name: str = Field(max_length=64)
    arguments: dict | str
    cloud: bool


@method("ai.call", AiCallP)
def ai_call(engine: Engine, p: AiCallP):
    return ai_tools.call_tool(engine.db, p.name, p.arguments, p.cloud)


class AiPromptP(P):
    provider_kind: str = Field(max_length=40)
    chart_context: dict | None = None


@method("ai.system_prompt", AiPromptP)
def ai_system_prompt(engine: Engine, p: AiPromptP):
    return {"prompt": ai_prompt.system_prompt(_today(engine).isoformat(),
                                              engine.db.get_pref("display_timezone", "Europe/Zagreb"), engine.demo,
                                              p.provider_kind, p.chart_context), "version": ai_prompt.PROMPT_VERSION}


@method("ai.private_habits")
def ai_private(engine: Engine):
    return sorted(private_habit_keys(engine.db))


class DisclosureP(P):
    thread_id: str | None = None
    provider: str = Field(max_length=40)
    model: str | None = Field(None, max_length=80)
    categories: list[str]
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload_bytes: int = Field(ge=0)


@method("ai.disclosure_log", DisclosureP)
def ai_disclosure_log(engine: Engine, p: DisclosureP):
    did = str(uuid.uuid4())
    engine.db.execute("INSERT INTO ai_disclosures(id, thread_id, provider, model, categories, payload_sha256,"
                      " payload_bytes, approved_at) VALUES (?,?,?,?,?,?,?,?)",
                      (did, p.thread_id, p.provider, p.model, json.dumps(p.categories), p.payload_sha256,
                       p.payload_bytes, utcnow()))
    return {"id": did}


@method("ai.disclosures")
def ai_disclosures(engine: Engine):
    return engine.db.query("SELECT * FROM ai_disclosures ORDER BY approved_at DESC LIMIT 500")


@method("chat.threads")
def chat_threads(engine: Engine):
    return engine.db.query("SELECT * FROM chat_threads ORDER BY updated_at DESC")


class ThreadCreateP(P):
    title: str = Field("New conversation", max_length=200)
    provider: str | None = Field(None, max_length=40)


@method("chat.thread_create", ThreadCreateP)
def chat_thread_create(engine: Engine, p: ThreadCreateP):
    tid, now = str(uuid.uuid4()), utcnow()
    engine.db.execute("INSERT INTO chat_threads(id, title, provider, created_at, updated_at) VALUES (?,?,?,?,?)",
                      (tid, p.title, p.provider, now, now))
    return engine.db.query_one("SELECT * FROM chat_threads WHERE id=?", (tid,))


class ThreadRenameP(IdP):
    title: str = Field(max_length=200)


@method("chat.thread_rename", ThreadRenameP)
def chat_thread_rename(engine: Engine, p: ThreadRenameP):
    engine.db.execute("UPDATE chat_threads SET title=?, updated_at=? WHERE id=?", (p.title, utcnow(), p.id))
    return {"ok": True}


@method("chat.thread_delete", IdP)
def chat_thread_delete(engine: Engine, p: IdP):
    engine.db.execute("DELETE FROM chat_threads WHERE id=?", (p.id,))
    return {"ok": True}


@method("chat.delete_all")
def chat_delete_all(engine: Engine):
    engine.db.execute("DELETE FROM chat_messages")
    engine.db.execute("DELETE FROM chat_threads")
    return {"ok": True}


class ThreadP(P):
    thread_id: str = Field(max_length=64)


@method("chat.messages", ThreadP)
def chat_messages(engine: Engine, p: ThreadP):
    rows = engine.db.query("SELECT * FROM chat_messages WHERE thread_id=? ORDER BY created_at", (p.thread_id,))
    for r in rows:
        for k in ("tool_calls", "evidence"):
            r[k] = json.loads(r[k]) if r[k] else None
    return rows


class MessageP(ThreadP):
    role: Literal["user", "assistant", "tool"]
    content: str = Field(max_length=200_000)
    tool_calls: list | None = None
    evidence: list | None = None
    provider: str | None = None
    model: str | None = None


@method("chat.append", MessageP)
def chat_append(engine: Engine, p: MessageP):
    mid, now = str(uuid.uuid4()), utcnow()
    engine.db.execute("INSERT INTO chat_messages(id, thread_id, role, content, tool_calls, evidence, provider, model,"
                      " created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                      (mid, p.thread_id, p.role, p.content, json.dumps(p.tool_calls) if p.tool_calls else None,
                       json.dumps(p.evidence) if p.evidence else None, p.provider, p.model, now))
    engine.db.execute("UPDATE chat_threads SET updated_at=? WHERE id=?", (now, p.thread_id))
    return {"id": mid, "created_at": now}


# ---------------------------------------------------------------------------------------------- reports
@method("report.build", RangeP)
def report_build(engine: Engine, p: RangeP):
    a, b = p.d()
    return reports.build(engine.db, a, b)
