"""Intraday timelines: exploratory Daily Energy Estimate (energy.v1) and low-movement arousal timeline (arousal.v1).

Energy is an *exploratory model* with hand-set, documented parameters. It is not validated and must not be read
as a measurement. Arousal marks heart rate above your reference resting HR during awake, low-movement periods
outside exercise; caffeine, heat, illness, posture, digestion and emotions can all cause it.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from .. import ALGORITHM_VERSIONS
from ..db.connection import Database
from ..timeutil import DEFAULT_TZ, day_bounds_utc
from .data import load_exercises, load_features, load_measurements, load_sleep_episodes
from .features import LOWMOVE_HOUR_STEPS, hr_max
from .scores import get_scores

ENERGY_VERSION = ALGORITHM_VERSIONS["energy"]
AROUSAL_VERSION = ALGORITHM_VERSIONS["arousal"]

ENERGY_PARAMS = {
    "bin_min": 15, "base_drain_per_h": 1.8, "activity_drain_per_h_at_hrr1": 9.0, "activity_exponent": 1.5,
    "arousal_drain_per_h": 1.6, "rest_recharge_per_h": 1.0, "sleep_recharge_per_h": 7.0,
    "start_from_recovery": "50 + 20 × recovery z, clipped to 15–95", "arousal_threshold_bpm": 12,
}


def _bins(d: date, tz: str, bin_min: int):
    a, b = day_bounds_utc(d, tz)
    idx = pd.date_range(a, b, freq=f"{bin_min}min", inclusive="left")
    return idx


def day_frame(db: Database, d: date, tz: str | None = None, bin_min: int = 15) -> tuple[pd.DataFrame, dict]:
    tz = tz or db.get_pref("display_timezone", DEFAULT_TZ)
    idx = _bins(d, tz, bin_min)
    fr = pd.DataFrame(index=idx)
    fr["state"] = "no_data"
    hr = load_measurements(db, ["heart_rate"], d - timedelta(days=1), d + timedelta(days=1))
    st = load_measurements(db, ["steps"], d - timedelta(days=1), d + timedelta(days=1))
    if not hr.empty:
        h = hr.set_index("start")["value"]
        h = h[(h.index >= idx[0]) & (h.index < idx[-1] + pd.Timedelta(minutes=bin_min))]
        fr["hr"] = h.resample(f"{bin_min}min", origin=idx[0]).mean().reindex(idx)
    else:
        fr["hr"] = np.nan
    fr["steps"] = 0.0
    if not st.empty:
        st = st[(st["end"] > idx[0]) & (st["start"] < idx[-1] + pd.Timedelta(minutes=bin_min))]
        for s, e, v in zip(st["start"], st["end"], st["value"]):
            span = max((e - s).total_seconds(), 60.0)
            cover = fr.index[(fr.index + pd.Timedelta(minutes=bin_min) > s) & (fr.index < e)]
            for b0 in cover:
                ov = (min(e, b0 + pd.Timedelta(minutes=bin_min)) - max(s, b0)).total_seconds()
                fr.at[b0, "steps"] += v * max(ov, 0) / span
    has_steps = not st.empty
    eps = load_sleep_episodes(db, d - timedelta(days=1), d + timedelta(days=1), tz)
    for ep in eps:
        m = (fr.index + pd.Timedelta(minutes=bin_min / 2) >= pd.Timestamp(ep.start)) & (
            fr.index + pd.Timedelta(minutes=bin_min / 2) <= pd.Timestamp(ep.end))
        fr.loc[m, "state"] = "sleep"
    ex = load_exercises(db, d - timedelta(days=1), d)
    for s, e in zip(ex.get("start", []), ex.get("end", [])):
        m = (fr.index + pd.Timedelta(minutes=bin_min) > s) & (fr.index < e)
        fr.loc[m & (fr["state"] != "sleep"), "state"] = "exercise"
    ctx = {"has_hr": not hr.empty, "has_steps": has_steps, "episodes": [e.to_dict() for e in eps]}
    feats = load_features(db, ["rhr", "lowmove_hr_median"], d - timedelta(days=15), d)
    prev = feats["rhr"].iloc[:-1].dropna()
    ref = float(prev.tail(14).median()) if len(prev) >= 3 else (float(feats["rhr"].iloc[-1]) if not pd.isna(
        feats["rhr"].iloc[-1]) else None)
    ctx["reference_rhr"] = ref
    pl = feats["lowmove_hr_median"].iloc[:-1].dropna()
    lref = float(pl.tail(14).median()) if len(pl) >= 3 else (float(feats["lowmove_hr_median"].iloc[-1]) if not pd.isna(
        feats["lowmove_hr_median"].iloc[-1]) else None)
    ctx["reference_lowmove_hr"] = lref
    hourly_steps = fr["steps"].groupby(fr.index.tz_convert(tz).hour).transform("sum")
    awake = fr["state"].isin(["no_data"]) & fr["hr"].notna()
    lowmove = awake & (hourly_steps < LOWMOVE_HOUR_STEPS) if has_steps else awake & False
    fr.loc[awake, "state"] = "awake"
    if lref is not None:
        elev = fr["hr"] - lref
        fr["elevation"] = elev
        fr.loc[lowmove & (elev >= ENERGY_PARAMS["arousal_threshold_bpm"]), "state"] = "elevated_low_movement"
        fr.loc[lowmove & (elev < ENERGY_PARAMS["arousal_threshold_bpm"]), "state"] = "calm_low_movement"
    else:
        fr["elevation"] = np.nan
    fr.loc[awake & (hourly_steps >= LOWMOVE_HOUR_STEPS) & ~fr["state"].isin(["exercise"]), "state"] = "active"
    return fr, ctx


def arousal_timeline(db: Database, d: date) -> dict:
    tz = db.get_pref("display_timezone", DEFAULT_TZ)
    fr, ctx = day_frame(db, d, tz)
    pts = [{"t": t.isoformat(), "hr": None if pd.isna(r.hr) else round(float(r.hr), 1),
            "steps": round(float(r.steps), 0), "state": r.state,
            "elevation": None if pd.isna(r.elevation) else round(float(r.elevation), 1)} for t, r in fr.iterrows()]
    states = fr["state"].value_counts().to_dict()
    lt = fr.index.tz_convert(tz)
    morning = fr[(lt.hour >= 6) & (lt.hour < 12)]
    afternoon = fr[(lt.hour >= 12) & (lt.hour < 18)]
    summ = {"minutes_by_state": {k: int(v) * 15 for k, v in states.items()}, "reference_rhr": ctx["reference_rhr"],
            "reference_lowmove_hr": ctx["reference_lowmove_hr"],
            "morning_elevated_min": int((morning["state"] == "elevated_low_movement").sum()) * 15,
            "afternoon_elevated_min": int((afternoon["state"] == "elevated_low_movement").sum()) * 15}
    missing = []
    if not ctx["has_hr"]:
        missing.append("heart rate")
    if not ctx["has_steps"]:
        missing.append("steps (needed to identify low-movement periods)")
    if ctx["reference_lowmove_hr"] is None:
        missing.append("reference low-movement heart rate (needs a few days of data)")
    return {"date": d.isoformat(), "algorithm": AROUSAL_VERSION, "points": pts, "summary": summ,
            "missing_inputs": missing, "status": "ok" if not missing else ("partial" if ctx["has_hr"] else
                                                                        "insufficient_data"),
            "interpretation_note": "Elevated heart rate at low movement is a physiological signal, not a measure of "
                                   "psychological stress. Caffeine, heat, illness, digestion, posture and emotions "
                                   "can all raise heart rate."}


def energy_timeline(db: Database, d: date) -> dict:
    tz = db.get_pref("display_timezone", DEFAULT_TZ)
    P = ENERGY_PARAMS
    fr, ctx = day_frame(db, d, tz, P["bin_min"])
    rec = get_scores(db, "recovery", d, d)
    rz = rec[0]["z"] if rec and rec[0]["z"] is not None else None
    missing, used = [], []
    if rz is None:
        missing.append("morning recovery estimate")
    if not ctx["has_hr"]:
        missing.append("heart rate")
    if not ctx["has_steps"]:
        missing.append("steps")
    hmax, hsrc = hr_max(db, d)
    ref = ctx["reference_rhr"]
    if hmax is None:
        missing.append("max heart rate (set birth year or max HR)")
    main = [e for e in ctx["episodes"] if e["wake_date"] == d.isoformat() and e["duration_min"] >= 180]
    if rz is None or not main:
        return {"date": d.isoformat(), "algorithm": ENERGY_VERSION, "status": "insufficient_data", "points": [],
                "missing_inputs": missing + ([] if main else ["main sleep ending today"]), "parameters": P,
                "note": "Energy needs a morning recovery estimate and a recorded main sleep."}
    wake = pd.Timestamp(datetime.fromisoformat(main[-1]["end"]))
    level = float(np.clip(50 + 20 * rz, 15, 95))
    start_level = level
    h = P["bin_min"] / 60.0
    drains = {"baseline": 0.0, "activity": 0.0, "arousal": 0.0}
    gains = {"rest": 0.0, "sleep": 0.0}
    pts = []
    for t, r in fr.iterrows():
        if t + pd.Timedelta(minutes=P["bin_min"]) <= wake:
            pts.append({"t": t.isoformat(), "value": None, "state": r.state})
            continue
        if r.state == "sleep":
            g = P["sleep_recharge_per_h"] * h * 0.85
            level += g
            gains["sleep"] += g
        else:
            dr = P["base_drain_per_h"] * h
            drains["baseline"] += dr
            act = 0.0
            if not pd.isna(r.hr) and ref is not None and hmax is not None and hmax > ref:
                hrr = float(np.clip((r.hr - ref) / (hmax - ref), 0, 1))
                act = P["activity_drain_per_h_at_hrr1"] * (hrr ** P["activity_exponent"]) * h
            elif ctx["has_steps"]:
                act = min(r.steps / 1500.0, 1.0) * P["activity_drain_per_h_at_hrr1"] * 0.4 * h
            drains["activity"] += act
            ar = P["arousal_drain_per_h"] * h if r.state == "elevated_low_movement" else 0.0
            drains["arousal"] += ar
            rest = P["rest_recharge_per_h"] * h if (r.state == "calm_low_movement" and not pd.isna(r.elevation)
                                                    and r.elevation <= 0) else 0.0
            gains["rest"] += rest
            level += rest - dr - act - ar
        level = float(np.clip(level, 0, 100))
        pts.append({"t": t.isoformat(), "value": round(level, 1), "state": r.state,
                    "hr": None if pd.isna(r.hr) else round(float(r.hr), 1)})
    vals = [p["value"] for p in pts if p["value"] is not None]
    used = ["morning recovery estimate", "sleep episodes"] + (["heart rate"] if ctx["has_hr"] else []) + (
        ["steps"] if ctx["has_steps"] else [])
    return {"date": d.isoformat(), "algorithm": ENERGY_VERSION, "status": "partial" if missing else "ok",
            "points": pts, "start_value": round(start_level, 1), "end_value": vals[-1] if vals else None,
            "min_value": min(vals) if vals else None, "drains": {k: round(v, 1) for k, v in drains.items()},
            "gains": {k: round(v, 1) for k, v in gains.items()}, "inputs_used": used, "missing_inputs": missing,
            "hr_max": hmax, "hr_max_source": hsrc, "reference_rhr": ref, "parameters": P,
            "limitations": ["Exploratory model with hand-set parameters; not validated against any reference.",
                            "Index units are arbitrary (0–100) and not a percentage of anything physiological."]}
