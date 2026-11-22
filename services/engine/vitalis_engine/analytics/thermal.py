"""Body temperature and bedroom temperature (thermal.v1-exploratory).

* Overnight skin temperature comes from the watch (Health Connect SkinTemperatureRecord: deviations from the source's
  own baseline, plus an absolute value when the source provides its baseline). VITALIS compares each night with the
  user's *personal* baseline (robust median of the previous nights, baseline.robust.v1).
* Bedroom temperature per night comes from a room sensor (metric `room_temperature`, e.g. a future SmartThings
  sensor) or, failing that, from the journal entry `bedroom_temp_c` made on the evening before.
* The bedroom-temperature suggestion is a transparent heuristic, not a validated model: general guidance for adults
  (about 16-19 degC; 18 degC as the starting point) is replaced by the user's own best 1.5 degC band once enough nights
  with varied bedroom temperatures exist and that band clearly beats the worst one (bootstrap 95 % CI of the sleep
  efficiency difference above 0), then nudged by recent overnight skin temperature and sleep disturbance.
  Nothing is controlled automatically.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from ..db.connection import Database
from . import stats
from .baseline import compute, settings
from .data import load_features, load_measurements, load_sleep_episodes
from .features import MAIN_SLEEP_MIN

THERMAL_VERSION = "thermal.v1-exploratory"
GENERAL_START_C = 18.0
GENERAL_RANGE_C = (16.0, 19.0)
MIN_PERSONAL_NIGHTS = 12
MIN_PERSONAL_SPREAD_C = 2.0
ELEVATED_C = 0.5          # personal deviation counted as "warmer than usual"
LIMITATIONS = [
    "Exploratory heuristic (thermal.v1-exploratory), not validated; it only suggests, nothing is controlled.",
    "Wrist skin temperature is affected by the room, bedding and how the watch sits; it is not core body temperature.",
    "Overnight data reach VITALIS after the watch syncs, usually after you wake, so the suggestion is for the next "
    "night, not a live adjustment.",
    "A warmer-than-usual night can accompany illness, alcohol, a warm room or the menstrual cycle; VITALIS does not "
    "diagnose fever or anything else.",
]


def _prefs(db: Database) -> tuple[float, float]:
    lo = float(db.get_pref("thermal_min_c", 16.0))
    hi = float(db.get_pref("thermal_max_c", 22.0))
    return (lo, hi) if lo < hi else (16.0, 22.0)


def _room_by_wake_date(db: Database, start: date, end: date) -> dict[date, tuple[float, str]]:
    """Bedroom temperature per night (dated by wake day): sensor during the main sleep, else last evening's journal."""
    out: dict[date, tuple[float, str]] = {}
    sensor = load_measurements(db, ["room_temperature"], start - timedelta(days=1), end)
    if not sensor.empty:
        main: dict[date, object] = {}
        for ep in load_sleep_episodes(db, start, end):  # main sleep = longest episode >= 3 h ending that day
            if ep.duration_min >= MAIN_SLEEP_MIN and (ep.wake_date not in main or ep.duration_min > main[ep.wake_date].duration_min):
                main[ep.wake_date] = ep
        for ep in main.values():
            m = sensor[(sensor["start"] >= pd.Timestamp(ep.start)) & (sensor["start"] <= pd.Timestamp(ep.end))]
            if len(m):
                out[ep.wake_date] = (float(m["value"].mean()), "sensor")
    j = load_features(db, ["j:bedroom_temp_c"], start - timedelta(days=1), end)["j:bedroom_temp_c"]
    for d, v in j.items():
        wake = d + timedelta(days=1)
        if not pd.isna(v) and wake not in out and start <= wake <= end:
            out[wake] = (float(v), "journal")
    return out


def nightly(db: Database, start: date, end: date) -> pd.DataFrame:
    feats = ["skin_temp_delta_night", "skin_temp_night", "sleep_efficiency", "sleep_waso_min", "sleep_awakenings",
             "sleep_latency_min", "sleep_tst_min", "rhr"]
    f = load_features(db, feats, start, end)
    room = _room_by_wake_date(db, start, end)
    f["bedroom_temp"] = [room.get(d, (np.nan, ""))[0] for d in f.index]
    f["bedroom_source"] = [room.get(d, (np.nan, None))[1] for d in f.index]
    # One skin series for personal comparison: deviation values when present, else absolute values.
    f["skin"] = f["skin_temp_delta_night"].where(f["skin_temp_delta_night"].notna(), f["skin_temp_night"])
    return f


def _personal_dev(f: pd.DataFrame, d: date, window: int, mp: int, ms: int):
    b = compute(f["skin"], "skin_temp_delta_night", d, window, mp, ms)
    v = f.at[d, "skin"] if d in f.index else np.nan
    if pd.isna(v) or b.median is None or b.status == "insufficient":
        return None, b
    return float(v - b.median), b


def summary(db: Database, start: date, end: date) -> dict:
    window, mp, ms = settings(db)
    f = nightly(db, start - timedelta(days=window), end)
    nights = []
    for d in f.loc[start:end].index:
        dev, b = _personal_dev(f, d, window, mp, ms)
        row = f.loc[d]
        nights.append({"date": d.isoformat(),
                       "skin_delta": None if pd.isna(row["skin_temp_delta_night"]) else float(row["skin_temp_delta_night"]),
                       "skin_abs": None if pd.isna(row["skin_temp_night"]) else float(row["skin_temp_night"]),
                       "personal_dev": dev, "baseline_status": b.status,
                       "bedroom_temp": None if pd.isna(row["bedroom_temp"]) else float(row["bedroom_temp"]),
                       "bedroom_source": row["bedroom_source"] if isinstance(row["bedroom_source"], str) else None,
                       "sleep_efficiency": None if pd.isna(row["sleep_efficiency"]) else float(row["sleep_efficiency"]),
                       "sleep_waso_min": None if pd.isna(row["sleep_waso_min"]) else float(row["sleep_waso_min"]),
                       "sleep_awakenings": None if pd.isna(row["sleep_awakenings"]) else float(row["sleep_awakenings"])})
    elevated = [n["date"] for n in nights if n["personal_dev"] is not None and n["personal_dev"] >= ELEVATED_C]
    cur = f.loc[start:end]
    corr = {
        "skin_vs_efficiency": stats.correlate(cur["skin"], cur["sleep_efficiency"], "spearman"),
        "skin_vs_awakenings": stats.correlate(cur["skin"], cur["sleep_awakenings"], "spearman"),
        "bedroom_vs_efficiency": stats.correlate(cur["bedroom_temp"], cur["sleep_efficiency"], "spearman"),
        "bedroom_vs_skin": stats.correlate(cur["bedroom_temp"], cur["skin"], "spearman"),
    }
    bt = load_measurements(db, ["body_temperature"], start, end)
    readings = [] if bt.empty else [{"time": t.isoformat(), "celsius": float(v), "provider": p}
                                    for t, v, p in zip(bt["start"], bt["value"], bt["provider"])]
    _, b_end = _personal_dev(f, end, window, mp, ms)
    return {"algorithm": THERMAL_VERSION, "nights": nights, "elevated_nights": elevated,
            "baseline": b_end.as_dict(), "correlations": corr, "body_temperature": readings[-200:],
            "has_skin": bool(cur["skin"].notna().any()), "has_absolute": bool(cur["skin_temp_night"].notna().any()),
            "nights_with_bedroom_temp": int(cur["bedroom_temp"].notna().sum()), "limitations": LIMITATIONS}


def _personal_optimum(pairs: pd.DataFrame, lo: float, hi: float, seed: int = 7):
    """Nights grouped into 1.5 degC bands of bedroom temperature (within the limits). The band with the highest mean
    sleep efficiency counts only if it beats the worst band clearly (bootstrap 95 % CI of the difference > 0)."""
    p = pairs[(pairs["bedroom_temp"] >= lo) & (pairs["bedroom_temp"] <= hi)]
    if len(p) < MIN_PERSONAL_NIGHTS:
        return None
    t0 = np.floor(float(p["bedroom_temp"].min()) * 2) / 2
    edges = np.arange(t0, float(p["bedroom_temp"].max()) + 1.5 + 1e-9, 1.5)
    bands = []
    for a, b in zip(edges[:-1], edges[1:]):
        g = p[(p["bedroom_temp"] >= a) & (p["bedroom_temp"] < b)]
        if len(g) >= 4:
            bands.append({"from": float(a), "to": float(b), "n": int(len(g)),
                          "mean_efficiency": float(g["sleep_efficiency"].mean()),
                          "median_temp": float(g["bedroom_temp"].median()), "_y": g["sleep_efficiency"].to_numpy(float)})
    if len(bands) < 2:
        return None
    best = max(bands, key=lambda x: x["mean_efficiency"])
    worst = min(bands, key=lambda x: x["mean_efficiency"])
    rng = np.random.default_rng(seed)
    diffs = [rng.choice(best["_y"], len(best["_y"])).mean() - rng.choice(worst["_y"], len(worst["_y"])).mean()
             for _ in range(2000)]
    ci = [float(np.percentile(diffs, 2.5)), float(np.percentile(diffs, 97.5))]
    for x in bands:
        x.pop("_y")
    return {"optimum": round(best["median_temp"] * 2) / 2, "interval": [best["from"], best["to"]], "n": int(len(p)),
            "best_band": best, "worst_band": worst,
            "difference_pp": best["mean_efficiency"] - worst["mean_efficiency"], "difference_ci": ci,
            "clear": ci[0] > 0, "bands": bands}


def suggestion(db: Database, on: date) -> dict:
    """Suggested bedroom temperature for the night starting on the evening of `on` (wake date on + 1)."""
    window, mp, ms = settings(db)
    lo_lim, hi_lim = _prefs(db)
    f = nightly(db, on - timedelta(days=window + 120), on)
    reasons: list[str] = []
    pairs = f.loc[on - timedelta(days=180):on, ["bedroom_temp", "sleep_efficiency"]].dropna()
    basis, base = "general", float(np.clip(GENERAL_START_C, lo_lim, hi_lim))
    interval = [max(lo_lim, GENERAL_RANGE_C[0]), min(hi_lim, GENERAL_RANGE_C[1])]
    personal = None
    if len(pairs) >= MIN_PERSONAL_NIGHTS and np.ptp(pairs["bedroom_temp"].to_numpy(float)) >= MIN_PERSONAL_SPREAD_C:
        personal = _personal_optimum(pairs, lo_lim, hi_lim)
        if personal and personal["clear"]:
            basis, base, interval = "personal", personal["optimum"], personal["interval"]
            bb, wb = personal["best_band"], personal["worst_band"]
            reasons.append(f"Your nights at {bb['from']:.1f}–{bb['to']:.1f} °C ({bb['n']} nights) had "
                           f"{personal['difference_pp']:.1f} percentage points higher sleep efficiency than nights at "
                           f"{wb['from']:.1f}–{wb['to']:.1f} °C ({wb['n']} nights); 95 % CI "
                           f"{personal['difference_ci'][0]:.1f}–{personal['difference_ci'][1]:.1f}. An association in "
                           "your own data, not proof of cause.")
        else:
            reasons.append(f"Your {len(pairs)} nights with a bedroom temperature do not yet show a clear best "
                           "temperature, so general guidance is used.")
    else:
        reasons.append(f"{len(pairs)} night(s) with a bedroom temperature recorded. Log it in the journal (or connect a "
                       f"room sensor); after about {MIN_PERSONAL_NIGHTS} nights at different temperatures (at least "
                       f"{MIN_PERSONAL_SPREAD_C:.0f} °C apart) VITALIS can personalise this.")
    if basis == "general":
        reasons.append("General guidance for adults is a cool bedroom of roughly 16–19 °C; 18 °C is the starting point.")

    adj = 0.0
    recent = [on - timedelta(days=i) for i in range(3)]
    devs = [d for d in (_personal_dev(f, r, window, mp, ms)[0] for r in recent) if d is not None]
    _, b = _personal_dev(f, on, window, mp, ms)
    skin_info = {"recent_personal_dev": float(np.mean(devs)) if devs else None, "nights": len(devs),
                 "baseline_status": b.status, "baseline_n": b.n}
    if devs:
        dev = float(np.mean(devs))
        waso_b = compute(f["sleep_waso_min"], "sleep_waso_min", on, window, mp, ms)
        eff_b = compute(f["sleep_efficiency"], "sleep_efficiency", on, window, mp, ms)
        rec = f.loc[recent[-1]:on]
        disturbed = ((waso_b.median is not None and rec["sleep_waso_min"].mean() > waso_b.median + 10)
                     or (eff_b.median is not None and rec["sleep_efficiency"].mean() < eff_b.median - 2))
        slow_onset = rec["sleep_latency_min"].mean() > 25 if rec["sleep_latency_min"].notna().any() else False
        if dev >= 0.6:
            adj = -1.0
            reasons.append(f"Your overnight skin temperature was {dev:+.1f} °C versus your usual over the last "
                           f"{len(devs)} night(s): suggesting 1 °C cooler.")
        elif dev >= 0.3 and disturbed:
            adj = -0.5
            reasons.append(f"Overnight skin temperature {dev:+.1f} °C above your usual and more wake time than usual: "
                           "suggesting 0.5 °C cooler.")
        elif dev <= -0.3 and slow_onset:
            adj = 0.5
            reasons.append(f"Overnight skin temperature {dev:+.1f} °C below your usual and slow sleep onset: "
                           "suggesting 0.5 °C warmer.")
        else:
            reasons.append(f"Overnight skin temperature is close to your usual ({dev:+.1f} °C): no adjustment.")
    else:
        reasons.append(f"Overnight skin temperature: personal baseline {b.status} ({b.n} nights; needs {mp}) — "
                       "no adjustment yet.")

    value = float(np.clip(round((base + adj) * 2) / 2, lo_lim, hi_lim))
    if value != round((base + adj) * 2) / 2:
        reasons.append(f"Kept within your limits of {lo_lim:.0f}–{hi_lim:.0f} °C.")
    return {"algorithm": THERMAL_VERSION, "night_of": on.isoformat(), "wake_date": (on + timedelta(days=1)).isoformat(),
            "suggested_c": value, "basis": basis, "interval": [round(interval[0], 1), round(interval[1], 1)],
            "adjustment_c": adj, "limits": [lo_lim, hi_lim], "reasons": reasons, "skin": skin_info,
            "personal_model": personal, "nights_with_bedroom_temp": int(len(pairs)),
            "how_to_apply": "Set your thermostat, air conditioner or radiator before bedtime, or use a SmartThings "
                            "routine. VITALIS does not change devices.",
            "limitations": LIMITATIONS}
