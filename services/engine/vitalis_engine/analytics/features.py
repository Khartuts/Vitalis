"""Daily feature computation (features.v1). See ALGORITHMS.md §1–§4 for definitions."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

from .. import ALGORITHM_VERSIONS
from ..db.connection import Database, utcnow
from ..model.canonical import ASLEEP_STAGES
from ..timeutil import DEFAULT_TZ, daterange, local_datetime
from .data import (SleepEpisode, load_exercises, load_measurements, load_sleep_episodes, pick_provider_per_day,
                   time_weights)

VERSION = ALGORITHM_VERSIONS["features"]
MAIN_SLEEP_MIN = 180.0          # episodes >= 3 h can be the main sleep
AWAKENING_MIN_BOUT = 2.0        # minutes; shorter awake bouts are not counted as awakenings
ZONE_EDGES = (0.5, 0.6, 0.7, 0.8, 0.9)  # fraction of HRmax (Edwards zones 1..5)
INACTIVE_HOUR_STEPS = 250
LOWMOVE_HOUR_STEPS = 300
AROUSAL_THRESHOLD_BPM = 12.0

FEATURE_INFO: dict[str, dict] = {
    "sleep_tst_min": {"label": "Total sleep time", "unit": "min", "group": "sleep", "better": "neutral"},
    "sleep_tib_min": {"label": "Time in bed", "unit": "min", "group": "sleep"},
    "sleep_efficiency": {"label": "Sleep efficiency", "unit": "%", "group": "sleep", "better": "higher"},
    "sleep_bedtime_min": {"label": "Bedtime (min from midnight)", "unit": "min", "group": "sleep"},
    "sleep_wake_min": {"label": "Wake time (min from midnight)", "unit": "min", "group": "sleep"},
    "sleep_midpoint_min": {"label": "Sleep midpoint (min from midnight)", "unit": "min", "group": "sleep"},
    "sleep_latency_min": {"label": "Sleep onset latency", "unit": "min", "group": "sleep", "better": "lower"},
    "sleep_waso_min": {"label": "Wake after sleep onset", "unit": "min", "group": "sleep", "better": "lower"},
    "sleep_awakenings": {"label": "Awakenings (≥2 min)", "unit": "count", "group": "sleep", "better": "lower"},
    "sleep_deep_min": {"label": "Deep sleep", "unit": "min", "group": "sleep"},
    "sleep_rem_min": {"label": "REM sleep", "unit": "min", "group": "sleep"},
    "sleep_light_min": {"label": "Light sleep", "unit": "min", "group": "sleep"},
    "sleep_awake_min": {"label": "Awake in bed", "unit": "min", "group": "sleep", "better": "lower"},
    "sleep_deep_pct": {"label": "Deep sleep share", "unit": "%", "group": "sleep"},
    "sleep_rem_pct": {"label": "REM share", "unit": "%", "group": "sleep"},
    "sleep_light_pct": {"label": "Light sleep share", "unit": "%", "group": "sleep"},
    "nap_min": {"label": "Naps", "unit": "min", "group": "sleep"},
    "sleep_score_imported": {"label": "Sleep score (Samsung)", "unit": "score", "group": "sleep",
                             "provenance": "imported"},
    "hr_sleep_mean": {"label": "Mean heart rate during sleep", "unit": "bpm", "group": "cardio", "better": "lower"},
    "hr_sleep_min": {"label": "Lowest heart rate during sleep", "unit": "bpm", "group": "cardio"},
    "rhr_sleep_est": {"label": "Overnight resting HR (calculated)", "unit": "bpm", "group": "cardio",
                      "better": "lower"},
    "rhr_reported": {"label": "Resting HR (source-reported)", "unit": "bpm", "group": "cardio", "better": "lower",
                     "provenance": "imported"},
    "rhr": {"label": "Resting heart rate", "unit": "bpm", "group": "cardio", "better": "lower"},
    "hrv_night_rmssd": {"label": "Nightly HRV (RMSSD, source)", "unit": "ms", "group": "cardio", "better": "higher",
                        "provenance": "imported"},
    "spo2_night_mean": {"label": "Overnight SpO2 mean", "unit": "%", "group": "cardio"},
    "spo2_night_min": {"label": "Overnight SpO2 lowest", "unit": "%", "group": "cardio"},
    "resp_rate_night": {"label": "Overnight respiratory rate", "unit": "br/min", "group": "cardio"},
    "skin_temp_delta_night": {"label": "Overnight skin temp deviation (source)", "unit": "°C Δ", "group": "cardio"},
    "skin_temp_night": {"label": "Overnight skin temperature", "unit": "°C", "group": "cardio"},
    "hr_day_mean": {"label": "Mean daytime heart rate", "unit": "bpm", "group": "cardio"},
    "hr_day_max": {"label": "Max heart rate", "unit": "bpm", "group": "cardio"},
    "hr_coverage_pct": {"label": "Heart-rate wear coverage", "unit": "%", "group": "quality"},
    "steps": {"label": "Steps", "unit": "count", "group": "activity", "better": "higher"},
    "active_kcal": {"label": "Active energy", "unit": "kcal", "group": "activity"},
    "distance_m": {"label": "Distance", "unit": "m", "group": "activity"},
    "floors": {"label": "Floors", "unit": "count", "group": "activity"},
    "exercise_min": {"label": "Exercise duration", "unit": "min", "group": "activity"},
    "exercise_count": {"label": "Exercise sessions", "unit": "count", "group": "activity"},
    "inactive_hours": {"label": "Inactive waking hours (<250 steps)", "unit": "h", "group": "activity",
                       "better": "lower"},
    "hr_z1_min": {"label": "HR zone 1 (50–60 % HRmax)", "unit": "min", "group": "activity"},
    "hr_z2_min": {"label": "HR zone 2 (60–70 %)", "unit": "min", "group": "activity"},
    "hr_z3_min": {"label": "HR zone 3 (70–80 %)", "unit": "min", "group": "activity"},
    "hr_z4_min": {"label": "HR zone 4 (80–90 %)", "unit": "min", "group": "activity"},
    "hr_z5_min": {"label": "HR zone 5 (90–100 %)", "unit": "min", "group": "activity"},
    "trimp": {"label": "Cardio load (Edwards TRIMP)", "unit": "AU", "group": "activity"},
    "arousal_min": {"label": "Elevated HR at low movement (≥12 bpm above your usual)", "unit": "min",
                    "group": "stress", "better": "lower"},
    "arousal_mean_elev": {"label": "HR vs your usual low-movement HR", "unit": "bpm", "group": "stress",
                          "better": "lower"},
    "lowmove_hr_median": {"label": "Low-movement awake heart rate", "unit": "bpm", "group": "stress"},
    "lowmove_coverage_min": {"label": "Low-movement awake time observed", "unit": "min", "group": "quality"},
    "weight": {"label": "Weight", "unit": "kg", "group": "body"},
    "body_fat": {"label": "Body fat", "unit": "%", "group": "body"},
    "bp_sys": {"label": "Systolic BP", "unit": "mmHg", "group": "cardio"},
    "bp_dia": {"label": "Diastolic BP", "unit": "mmHg", "group": "cardio"},
    "vo2max": {"label": "VO2 max (source)", "unit": "mL/kg/min", "group": "cardio", "provenance": "imported"},
    "energy_score_imported": {"label": "Energy Score (Samsung)", "unit": "score", "group": "recovery",
                              "provenance": "imported"},
    "stress_vendor_mean": {"label": "Vendor stress score (mean)", "unit": "score", "group": "stress",
                           "provenance": "imported"},
    "recovery_index": {"label": "Morning recovery estimate (index)", "unit": "index", "group": "recovery",
                       "better": "higher", "provenance": "estimated"},
    "sleep_sri_14d": {"label": "Sleep regularity index (14 d)", "unit": "SRI", "group": "sleep", "better": "higher"},
    "sleep_debt_min": {"label": "Estimated sleep debt", "unit": "min", "group": "sleep", "better": "lower"},
}


def hr_max(db: Database, on: date) -> tuple[float | None, str]:
    v = db.get_pref("hr_max")
    if v:
        return float(v), "user-set"
    by = db.get_pref("birth_year")
    if by:
        age = on.year - int(by)
        return 208.0 - 0.7 * age, "Tanaka 208−0.7×age"
    return None, "unavailable (set max HR or birth year)"


class _Acc:
    def __init__(self):
        self.vals: dict[tuple[str, str], tuple[float | None, dict | None]] = {}

    def put(self, d: date, f: str, v, detail: dict | None = None):
        if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
            return
        self.vals[(d.isoformat(), f)] = (float(v), detail)


def _stage_minutes(ep: SleepEpisode) -> dict:
    mins = defaultdict(float)
    for st, a, b in ep.stages:
        mins[st] += (b - a).total_seconds() / 60.0
    return mins


def sleep_metrics(ep: SleepEpisode, tz: str) -> dict:
    """Per-episode sleep metrics. Missing measurements are left absent, never inferred."""
    out: dict = {}
    oob = 0.0
    if ep.has_stages:
        mins = _stage_minutes(ep)
        oob = mins.get("out_of_bed", 0.0)
        asleep = sum(mins.get(s, 0.0) for s in ASLEEP_STAGES)
        awake = mins.get("awake", 0.0) + mins.get("awake_in_bed", 0.0)
        tib = ep.duration_min - oob
        out.update(tst=asleep, tib=tib, awake=awake, deep=mins.get("deep"), rem=mins.get("rem"),
                   light=mins.get("light"))
        if tib > 0:
            out["efficiency"] = 100.0 * asleep / tib
        typed = mins.get("deep", 0) + mins.get("rem", 0) + mins.get("light", 0)
        if typed > 0 and asleep > 0 and mins.get("asleep", 0.0) < 0.05 * asleep:
            for k in ("deep", "rem", "light"):
                out[f"{k}_pct"] = 100.0 * mins.get(k, 0.0) / asleep
        sleep_segs = [(a, b) for s, a, b in ep.stages if s in ASLEEP_STAGES]
        if sleep_segs:
            onset, final = sleep_segs[0][0], max(b for _, b in sleep_segs)
            first = ep.stages[0]
            if first[0] in ("awake", "awake_in_bed") and first[1] <= ep.start + timedelta(minutes=1):
                out["latency"] = (onset - ep.start).total_seconds() / 60.0
            bouts, cur_a, cur_b, waso = [], None, None, 0.0
            for s, a, b in ep.stages:
                if a < onset or b > final:
                    continue
                if s in ("awake", "awake_in_bed", "out_of_bed"):
                    waso += (b - a).total_seconds() / 60.0
                    if cur_b is not None and (a - cur_b).total_seconds() <= 60:
                        cur_b = b
                    else:
                        if cur_a is not None:
                            bouts.append((cur_b - cur_a).total_seconds() / 60.0)
                        cur_a, cur_b = a, b
            if cur_a is not None:
                bouts.append((cur_b - cur_a).total_seconds() / 60.0)
            out["waso"] = waso
            out["awakenings"] = float(sum(1 for b in bouts if b >= AWAKENING_MIN_BOUT))
    else:
        out["tst"] = ep.duration_min
        out["tst_basis"] = "session duration (no stages supplied)"
    ref = ep.wake_date
    start_off = getattr(ep, "start_offset_eff", ep.offset_min)
    out["bedtime"] = _clock_minutes(local_datetime(ep.start, start_off, tz), ref)
    out["wake"] = _clock_minutes(local_datetime(ep.end, ep.end_offset_min, tz), ref)
    out["midpoint"] = (out["bedtime"] + out["wake"]) / 2.0
    return out


def _clock_minutes(dt_local: datetime, ref_day: date) -> float:
    """Wall-clock minutes relative to midnight of ref_day (DST-safe: compares clock readings, not elapsed time)."""
    days = (dt_local.date() - ref_day).days
    return days * 1440.0 + dt_local.hour * 60 + dt_local.minute + dt_local.second / 60.0


def _window_values(times: np.ndarray, values: np.ndarray, a: datetime, b: datetime) -> np.ndarray:
    lo = np.searchsorted(times, np.datetime64(a.astimezone(timezone.utc).replace(tzinfo=None), "ns"), "left")
    hi = np.searchsorted(times, np.datetime64(b.astimezone(timezone.utc).replace(tzinfo=None), "ns"), "right")
    return values[lo:hi], lo, hi


def compute_features(db: Database, start: date, end: date, tz: str | None = None) -> dict:
    tz = tz or db.get_pref("display_timezone", DEFAULT_TZ)
    acc = _Acc()
    lo, hi = start - timedelta(days=1), end + timedelta(days=1)

    # ---------------- sleep ----------------
    episodes = load_sleep_episodes(db, start, end, tz)
    main_by_day: dict[date, SleepEpisode] = {}
    naps: dict[date, float] = defaultdict(float)
    for ep in episodes:
        cur = main_by_day.get(ep.wake_date)
        if ep.duration_min >= MAIN_SLEEP_MIN and (cur is None or ep.duration_min > cur.duration_min):
            if cur is not None:
                naps[ep.wake_date] += cur.duration_min
            main_by_day[ep.wake_date] = ep
        else:
            naps[ep.wake_date] += ep.duration_min
    for d, ep in main_by_day.items():
        sm = sleep_metrics(ep, tz)
        det = {"record_ids": ep.record_ids, "providers": sorted(set(ep.providers)), "has_stages": ep.has_stages,
               "start": ep.start.isoformat(), "end": ep.end.isoformat()}
        acc.put(d, "sleep_tst_min", sm.get("tst"), {**det, "basis": sm.get("tst_basis", "sum of sleep stages")})
        for k, f in (("tib", "sleep_tib_min"), ("efficiency", "sleep_efficiency"), ("latency", "sleep_latency_min"),
                     ("waso", "sleep_waso_min"), ("awakenings", "sleep_awakenings"), ("deep", "sleep_deep_min"),
                     ("rem", "sleep_rem_min"), ("light", "sleep_light_min"), ("awake", "sleep_awake_min"),
                     ("deep_pct", "sleep_deep_pct"), ("rem_pct", "sleep_rem_pct"), ("light_pct", "sleep_light_pct"),
                     ("bedtime", "sleep_bedtime_min"), ("wake", "sleep_wake_min"),
                     ("midpoint", "sleep_midpoint_min")):
            acc.put(d, f, sm.get(k), det if f in ("sleep_tib_min",) else None)
    for d in daterange(start, end):
        if d in main_by_day or naps.get(d):
            acc.put(d, "nap_min", naps.get(d, 0.0))

    # ---------------- heart rate & overnight vitals ----------------
    hr = load_measurements(db, ["heart_rate"], lo, hi)
    hr_t = (hr["start"].dt.tz_convert("UTC").dt.tz_localize(None).to_numpy().astype("datetime64[ns]")
            if not hr.empty else np.array([], "datetime64[ns]"))
    hr_v = hr["value"].to_numpy() if not hr.empty else np.array([])
    other = load_measurements(db, ["hrv_rmssd", "hrv_rmssd_daily", "spo2", "spo2_daily_avg", "respiratory_rate",
                                   "skin_temp_delta", "skin_temperature", "resting_heart_rate", "sleep_score",
                                   "energy_score", "stress_score_vendor", "weight", "body_fat", "bp_systolic",
                                   "bp_diastolic", "vo2max"], lo, hi)
    by_metric = {m: g for m, g in other.groupby("metric")} if not other.empty else {}

    def series(metric):
        g = by_metric.get(metric)
        if g is None or g.empty:
            return np.array([], "datetime64[ns]"), np.array([])
        return (g["start"].dt.tz_convert("UTC").dt.tz_localize(None).to_numpy().astype("datetime64[ns]"),
                g["value"].to_numpy())

    hrv_t, hrv_v = series("hrv_rmssd")
    spo_t, spo_v = series("spo2")
    rr_t, rr_v = series("respiratory_rate")
    std_t, std_v = series("skin_temp_delta")
    st_t, st_v = series("skin_temperature")
    for d, ep in main_by_day.items():
        vals, a_i, b_i = _window_values(hr_t, hr_v, ep.start, ep.end)
        if len(vals) >= 6:
            acc.put(d, "hr_sleep_mean", float(np.mean(vals)), {"n": int(len(vals))})
            acc.put(d, "hr_sleep_min", float(np.min(vals)))
            s = pd.Series(vals, index=pd.DatetimeIndex(hr_t[a_i:b_i]))
            roll = s.rolling("30min", min_periods=3).mean()
            if roll.notna().any():
                acc.put(d, "rhr_sleep_est", float(roll.min()),
                        {"method": "lowest 30-min rolling mean of HR during main sleep", "n": int(len(vals))})
        hv, _, _ = _window_values(hrv_t, hrv_v, ep.start - timedelta(minutes=30), ep.end + timedelta(minutes=30))
        if len(hv):
            acc.put(d, "hrv_night_rmssd", float(np.mean(hv)),
                    {"method": "mean of source RMSSD values during main sleep", "n": int(len(hv))})
        sv, _, _ = _window_values(spo_t, spo_v, ep.start, ep.end)
        if len(sv) >= 3:
            acc.put(d, "spo2_night_mean", float(np.mean(sv)), {"n": int(len(sv))})
            acc.put(d, "spo2_night_min", float(np.min(sv)), {"n": int(len(sv))})
        rv, _, _ = _window_values(rr_t, rr_v, ep.start - timedelta(hours=1), ep.end + timedelta(hours=2))
        if len(rv):
            acc.put(d, "resp_rate_night", float(np.mean(rv)), {"n": int(len(rv))})
        tv, _, _ = _window_values(std_t, std_v, ep.start, ep.end)
        if len(tv):
            acc.put(d, "skin_temp_delta_night", float(np.mean(tv)), {"n": int(len(tv)), "note": "relative to source baseline"})
        tv2, _, _ = _window_values(st_t, st_v, ep.start, ep.end)
        if len(tv2):
            acc.put(d, "skin_temp_night", float(np.mean(tv2)), {"n": int(len(tv2))})

    def daily_from(metric, feature, agg="mean", extra=None):
        g = by_metric.get(metric)
        if g is None:
            return
        for ld, gg in g.groupby("local_date"):
            d = date.fromisoformat(ld)
            if start <= d <= end:
                v = gg["value"].iloc[-1] if agg == "last" else getattr(gg["value"], agg)()
                acc.put(d, feature, float(v), {"n": int(len(gg)), **(extra or {})})

    daily_from("resting_heart_rate", "rhr_reported", "mean", {"provenance": "imported"})
    daily_from("sleep_score", "sleep_score_imported", "last")
    daily_from("energy_score", "energy_score_imported", "last")
    daily_from("stress_score_vendor", "stress_vendor_mean", "mean")
    daily_from("weight", "weight", "last")
    daily_from("body_fat", "body_fat", "last")
    daily_from("bp_systolic", "bp_sys", "mean")
    daily_from("bp_diastolic", "bp_dia", "mean")
    daily_from("vo2max", "vo2max", "last")
    g = by_metric.get("hrv_rmssd_daily")
    if g is not None:
        for ld, gg in g.groupby("local_date"):
            d = date.fromisoformat(ld)
            if start <= d <= end and (d.isoformat(), "hrv_night_rmssd") not in acc.vals:
                acc.put(d, "hrv_night_rmssd", float(gg["value"].mean()),
                        {"method": "source daily RMSSD summary", "n": int(len(gg))})
    g = by_metric.get("spo2_daily_avg")
    if g is not None:
        for ld, gg in g.groupby("local_date"):
            d = date.fromisoformat(ld)
            if start <= d <= end and (d.isoformat(), "spo2_night_mean") not in acc.vals:
                acc.put(d, "spo2_night_mean", float(gg["value"].mean()), {"method": "source nightly average"})
    for d in daterange(start, end):
        k = d.isoformat()
        if (k, "rhr_sleep_est") in acc.vals:
            acc.put(d, "rhr", acc.vals[(k, "rhr_sleep_est")][0], {"method": "calculated overnight (preferred)"})
        elif (k, "rhr_reported") in acc.vals:
            acc.put(d, "rhr", acc.vals[(k, "rhr_reported")][0], {"method": "source-reported (fallback)"})

    # ---------------- activity ----------------
    act = load_measurements(db, ["steps", "steps_daily", "active_energy", "distance", "floors"], lo, hi)
    hourly_steps: dict[date, dict[int, float]] = defaultdict(dict)
    if not act.empty:
        for metric, feature in (("steps", "steps"), ("active_energy", "active_kcal"), ("distance", "distance_m"),
                                ("floors", "floors")):
            sub = pick_provider_per_day(act[act["metric"] == metric], db)
            if sub.empty:
                continue
            if metric == "steps":
                # Within one provider, prefer the de-duplicated hourly aggregate rows when present.
                is_agg = sub["meta"].fillna("").str.contains('"aggregate": true')
                if is_agg.any():
                    sub = sub[is_agg | ~sub["local_date"].isin(sub.loc[is_agg, "local_date"])]
            for ld, gg in sub.groupby("local_date"):
                d = date.fromisoformat(ld)
                if start <= d <= end:
                    acc.put(d, feature, float(gg["value"].sum()),
                            {"providers": sorted(gg["provider"].unique().tolist()), "n": int(len(gg))})
                    if metric == "steps":
                        for t, v in zip(gg["start"], gg["value"]):
                            lt = t.tz_convert(tz)
                            hourly_steps[d][lt.hour] = hourly_steps[d].get(lt.hour, 0.0) + float(v)
        daily = pick_provider_per_day(act[act["metric"] == "steps_daily"], db)
        for ld, gg in daily.groupby("local_date"):
            d = date.fromisoformat(ld)
            if start <= d <= end:
                acc.put(d, "steps", float(gg["value"].max()),
                        {"providers": sorted(gg["provider"].unique().tolist()), "basis": "source daily aggregate"})

    ex = load_exercises(db, start, end)
    if not ex.empty:
        ex = ex.assign(minutes=(ex["end"] - ex["start"]).dt.total_seconds() / 60.0)
        for ld, gg in ex.groupby("local_date"):
            d = date.fromisoformat(ld)
            acc.put(d, "exercise_min", float(gg["minutes"].sum()), {"sessions": gg["record_id"].tolist()})
            acc.put(d, "exercise_count", float(len(gg)))
    ex_windows = [(a.to_pydatetime(), b.to_pydatetime()) for a, b in zip(ex["start"], ex["end"])] if not ex.empty else []

    # ---------------- heart-rate days: coverage, zones, TRIMP, arousal ----------------
    if len(hr_t):
        hr_local = hr.assign(local_hour=hr["start"].dt.tz_convert(tz).dt.hour)
        weights = time_weights(hr["start"])
        hr_local = hr_local.assign(w=weights)
        lowmove: dict[date, pd.DataFrame] = {}
        for ld, gg in hr_local.groupby("local_date"):
            d = date.fromisoformat(ld)
            if not (start <= d <= end):
                continue
            ep = main_by_day.get(d)
            nxt = main_by_day.get(d + timedelta(days=1))
            t = gg["start"]
            asleep_mask = np.zeros(len(gg), bool)
            for e in (ep, nxt):
                if e is not None:
                    asleep_mask |= ((t >= pd.Timestamp(e.start)) & (t <= pd.Timestamp(e.end))).to_numpy()
            covered = float(gg["w"].sum())
            day_len = 1440.0
            acc.put(d, "hr_coverage_pct", min(100.0, 100.0 * covered / day_len), {"samples": int(len(gg))})
            awake = gg[~asleep_mask]
            if len(awake) >= 6:
                acc.put(d, "hr_day_mean", float(awake["value"].mean()))
            acc.put(d, "hr_day_max", float(gg["value"].max()))
            hmax, hsrc = hr_max(db, d)
            if hmax:
                frac = gg["value"].to_numpy() / hmax
                zmins = []
                for zi, lo_e in enumerate(ZONE_EDGES):
                    hi_e = ZONE_EDGES[zi + 1] if zi + 1 < len(ZONE_EDGES) else 10.0
                    zmins.append(float(gg["w"].to_numpy()[(frac >= lo_e) & (frac < hi_e)].sum()))
                for zi, m in enumerate(zmins):
                    acc.put(d, f"hr_z{zi + 1}_min", m)
                acc.put(d, "trimp", float(sum((i + 1) * m for i, m in enumerate(zmins))),
                        {"hr_max": round(hmax, 1), "hr_max_source": hsrc, "coverage_pct": round(100 * covered / day_len, 1)})
            # inactive hours: hours with HR present (watch worn), awake, steps < threshold
            hs = hourly_steps.get(d, {})
            if hs or (d.isoformat(), "steps") in acc.vals:
                worn_hours = set(awake["local_hour"].unique().tolist())
                inactive = sum(1 for h in worn_hours if hs.get(h, 0.0) < INACTIVE_HOUR_STEPS)
                acc.put(d, "inactive_hours", float(inactive), {"worn_awake_hours": len(worn_hours),
                                                               "threshold_steps": INACTIVE_HOUR_STEPS})
            # low-movement awake HR (outside exercise ±10 min) — reference data for the arousal index
            if len(awake) and hs:
                in_ex = np.zeros(len(awake), bool)
                at = awake["start"]
                for a, b in ex_windows:
                    in_ex |= ((at >= pd.Timestamp(a) - pd.Timedelta(minutes=10)) &
                              (at <= pd.Timestamp(b) + pd.Timedelta(minutes=10))).to_numpy()
                low = np.array([hs.get(h, 0.0) < LOWMOVE_HOUR_STEPS for h in awake["local_hour"]]) & ~in_ex
                lm = awake[low]
                if len(lm) >= 6:
                    lowmove[d] = lm
                    acc.put(d, "lowmove_hr_median", float(lm["value"].median()), {"n": int(len(lm))})
                    acc.put(d, "lowmove_coverage_min", float(lm["w"].sum()))
        # arousal: minutes ≥ threshold above the personal low-movement reference (median of prior 14 days)
        ref_by_day = _reference_lowmove(acc, start, end)
        for d, lm in lowmove.items():
            ref = ref_by_day.get(d)
            if ref is None:
                continue
            elev = lm["value"].to_numpy() - ref
            acc.put(d, "arousal_min", float(lm["w"].to_numpy()[elev >= AROUSAL_THRESHOLD_BPM].sum()),
                    {"reference_lowmove_hr": round(ref, 1), "threshold_bpm": AROUSAL_THRESHOLD_BPM, "n": int(len(lm))})
            acc.put(d, "arousal_mean_elev", float(np.average(elev, weights=lm["w"].to_numpy() + 1e-9)))

    # ---------------- journal ----------------
    for r in db.query("SELECT e.local_date, e.habit_key, e.value_num, e.value_text, h.value_type FROM journal_entries e"
                      " JOIN journal_habits h ON h.key=e.habit_key WHERE e.local_date BETWEEN ? AND ?",
                      (start.isoformat(), end.isoformat())):
        v = journal_numeric(r["value_type"], r["value_num"], r["value_text"])
        if v is not None:
            acc.put(date.fromisoformat(r["local_date"]), f"j:{r['habit_key']}", v)

    return {"values": acc.vals, "episodes": {d.isoformat(): ep.to_dict() for d, ep in main_by_day.items()}}


def journal_numeric(value_type: str, value_num, value_text) -> float | None:
    if value_type in ("boolean", "number", "scale"):
        return None if value_num is None else float(value_num)
    if value_type == "time" and value_text:
        hh, mm = value_text.split(":")[:2]
        return float(int(hh) * 60 + int(mm))
    return None


def _reference_lowmove(acc: _Acc, start: date, end: date) -> dict[date, float]:
    """Reference low-movement awake HR per day: median of the previous 14 days (falls back to the same day)."""
    vals = {date.fromisoformat(k[0]): v[0] for k, v in acc.vals.items() if k[1] == "lowmove_hr_median"}
    out = {}
    for d in daterange(start, end):
        prev = [vals[x] for x in (d - timedelta(days=i) for i in range(1, 15)) if x in vals]
        if len(prev) >= 3:
            out[d] = float(np.median(prev))
        elif d in vals:
            out[d] = vals[d]
    return out


def store_features(db: Database, result: dict, start: date, end: date) -> int:
    now = utcnow()
    rows = [(d, f, v, VERSION, json.dumps(det) if det else None, now) for (d, f), (v, det) in result["values"].items()]
    with db.transaction() as conn:
        conn.execute("DELETE FROM daily_features WHERE local_date BETWEEN ? AND ? AND feature NOT IN"
                     " ('sleep_sri_14d','sleep_debt_min','recovery_index')", (start.isoformat(), end.isoformat()))
        conn.executemany("INSERT OR REPLACE INTO daily_features(local_date, feature, value, algo_version, detail,"
                         " computed_at) VALUES (?,?,?,?,?,?)", rows)
    return len(rows)


def feature_info(name: str) -> dict:
    if name.startswith("j:"):
        return {"label": f"Journal: {name[2:]}", "unit": "", "group": "journal", "provenance": "user_entered"}
    return FEATURE_INFO.get(name, {"label": name, "unit": "", "group": "other"})
