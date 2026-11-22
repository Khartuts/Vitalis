"""Deterministic insight engine (insights.v1). Conservative thresholds, minimum observations, dedup and
contradiction suppression. AI may later rephrase insights, but never creates them."""

from __future__ import annotations

import json
import uuid
from datetime import date, timedelta

import numpy as np
import pandas as pd
from scipy import stats as sps

from .. import ALGORITHM_VERSIONS
from ..db.connection import Database, utcnow
from .baseline import compute, settings
from .data import load_features
from .features import feature_info
from .scores import get_scores
from .stats import scan

VERSION = ALGORITHM_VERSIONS["insights"]


def _ins(category, severity, title, observation, start, end, numbers, baseline, completeness, confidence, action,
         evidence, key):
    return {"id": str(uuid.uuid4()), "dedup_key": key, "category": category, "severity": severity, "title": title,
            "observation": observation, "period_start": start.isoformat(), "period_end": end.isoformat(),
            "numbers": numbers, "baseline": baseline, "completeness": completeness, "confidence": confidence,
            "action": action, "evidence": evidence, "algo_version": VERSION}


def _fmt_hm(minutes: float) -> str:
    m = int(round(abs(minutes)))
    return f"{m // 60} h {m % 60:02d} min"


def generate(db: Database, today: date) -> list[dict]:
    window, mp, ms = settings(db)
    feats = ["rhr", "hrv_night_rmssd", "sleep_tst_min", "sleep_sri_14d", "steps", "sleep_debt_min",
             "sleep_bedtime_min"]
    f = load_features(db, feats, today - timedelta(days=window + 120), today)
    out: list[dict] = []

    # 1. Resting HR elevated for 3 nights
    last3 = [today - timedelta(days=i) for i in range(3)]
    b = compute(f["rhr"], "rhr", last3[-1], window, mp, ms)
    vals = [f.at[d, "rhr"] for d in last3 if not pd.isna(f.at[d, "rhr"])]
    if b.status != "insufficient" and len(vals) >= 2:
        zs = [b.z(v) for v in vals]
        if all(z is not None and z >= 1.5 for z in zs):
            out.append(_ins("cardio", "attention", "Resting heart rate higher than usual",
                            f"Your overnight resting heart rate averaged {np.mean(vals):.0f} bpm over the last "
                            f"{len(vals)} nights, above your usual {b.median:.0f} bpm.", last3[-1], today,
                            {"recent_mean": float(np.mean(vals)), "baseline_median": b.median, "z": zs},
                            f"median {b.median:.0f} bpm over {b.n} days ({b.status})", len(vals) / 3,
                            "moderate" if b.status == "stable" else "low",
                            "Common causes include short or late sleep, alcohol, intense training, heat or an "
                            "oncoming illness. Prioritize rest and recheck tomorrow; seek medical advice if you feel "
                            "unwell or it persists for a week.",
                            [{"feature": "rhr", "start": last3[-1].isoformat(), "end": today.isoformat()}],
                            f"rhr_high:{today.isocalendar()[1]}"))

    # 1b. Overnight skin temperature above the personal baseline on 2 of the last 3 nights (thermal.v1)
    sk = load_features(db, ["skin_temp_delta_night", "skin_temp_night"], today - timedelta(days=window + 10), today)
    skin = sk["skin_temp_delta_night"].where(sk["skin_temp_delta_night"].notna(), sk["skin_temp_night"])
    bs = compute(skin, "skin_temp_delta_night", last3[-1], window, mp, ms)
    if bs.status != "insufficient":
        devs = [float(skin[d] - bs.median) for d in last3 if not pd.isna(skin[d])]
        warm = [x for x in devs if x >= 0.5]
        if len(warm) >= 2:
            out.append(_ins("temperature", "notice", "Overnight skin temperature higher than usual",
                            f"Your overnight skin temperature was {np.mean(warm):+.1f} °C above your usual on "
                            f"{len(warm)} of the last {len(devs)} nights.", last3[-1], today,
                            {"personal_deviation_c": devs, "baseline_median": bs.median},
                            f"median over {bs.n} nights ({bs.status})", len(devs) / 3,
                            "moderate" if bs.status == "stable" else "low",
                            "This can accompany illness, alcohol, a warm bedroom or the menstrual cycle. A slightly "
                            "cooler bedroom may help sleep (see Temperature). Wrist skin temperature is not a fever "
                            "measurement; use a thermometer and seek medical advice if you feel unwell.",
                            [{"feature": "skin_temp_delta_night", "start": last3[-1].isoformat(), "end": today.isoformat()}],
                            f"skin_warm:{today.isocalendar()[1]}"))

    # 2. Sleep duration below baseline over 7 nights
    wk = [today - timedelta(days=i) for i in range(7)]
    tst = f.loc[wk[-1]:today, "sleep_tst_min"].dropna()
    bt = compute(f["sleep_tst_min"], "sleep_tst_min", wk[-1], window, mp, ms)
    if len(tst) >= 5 and bt.status != "insufficient":
        diff = tst.mean() - bt.median
        if diff <= -30:
            out.append(_ins("sleep", "notice", "Shorter sleep this week",
                            f"You slept {_fmt_hm(tst.mean())} on average over the last {len(tst)} nights, "
                            f"{_fmt_hm(-diff)} less than your usual {_fmt_hm(bt.median)}.", wk[-1], today,
                            {"mean_tst_min": float(tst.mean()), "baseline_median_min": bt.median,
                             "difference_min": float(diff), "nights": int(len(tst))},
                            f"median over {bt.n} nights", len(tst) / 7, "moderate",
                            "Try the Sleep Planner to set a consistent bedtime that fits your wake time.",
                            [{"feature": "sleep_tst_min", "start": wk[-1].isoformat(), "end": today.isoformat()}],
                            f"sleep_short:{today.isocalendar()[1]}"))
        elif diff >= 30:
            out.append(_ins("sleep", "info", "More sleep than usual this week",
                            f"Average sleep {_fmt_hm(tst.mean())}, {_fmt_hm(diff)} above your usual.", wk[-1], today,
                            {"mean_tst_min": float(tst.mean()), "baseline_median_min": bt.median},
                            f"median over {bt.n} nights", len(tst) / 7, "moderate", None,
                            [{"feature": "sleep_tst_min", "start": wk[-1].isoformat(), "end": today.isoformat()}],
                            f"sleep_long:{today.isocalendar()[1]}"))

    # 3. Sleep regularity change (SRI, 14 d vs previous 14 d)
    sri_now = f.at[today, "sleep_sri_14d"] if today in f.index else np.nan
    prev_day = today - timedelta(days=14)
    sri_prev = f.at[prev_day, "sleep_sri_14d"] if prev_day in f.index else np.nan
    if not pd.isna(sri_now) and not pd.isna(sri_prev) and abs(sri_now - sri_prev) >= 10:
        better = sri_now > sri_prev
        out.append(_ins("sleep", "info", "Sleep schedule more consistent" if better else "Sleep schedule less consistent",
                        f"Sleep Regularity Index {sri_now:.0f} vs {sri_prev:.0f} two weeks earlier.",
                        prev_day, today, {"sri_now": float(sri_now), "sri_prev": float(sri_prev)}, None, None, "moderate",
                        None if better else "Keeping bed and wake times within about 30 minutes daily usually helps.",
                        [{"feature": "sleep_sri_14d", "start": (today - timedelta(days=28)).isoformat(),
                          "end": today.isoformat()}], f"sri:{today.isocalendar()[1]}:{'up' if better else 'down'}"))

    # 4. HRV change (7-day mean vs baseline)
    hv = f.loc[wk[-1]:today, "hrv_night_rmssd"].dropna()
    bh = compute(f["hrv_night_rmssd"], "hrv_night_rmssd", wk[-1], window, mp, ms)
    if len(hv) >= 4 and bh.status != "insufficient":
        z = (hv.mean() - bh.median) / bh.robust_sd
        if abs(z) >= 1.0:
            out.append(_ins("cardio", "notice" if z < 0 else "info",
                            "Overnight HRV lower than usual" if z < 0 else "Overnight HRV higher than usual",
                            f"Source-reported nightly HRV averaged {hv.mean():.0f} ms over {len(hv)} nights vs your "
                            f"usual {bh.median:.0f} ms.", wk[-1], today,
                            {"mean_ms": float(hv.mean()), "baseline_ms": bh.median, "z": float(z)},
                            f"median over {bh.n} nights", len(hv) / 7, "moderate" if len(hv) >= 6 else "low",
                            "Lower HRV often follows poor sleep, alcohol, hard training or illness." if z < 0 else None,
                            [{"feature": "hrv_night_rmssd", "start": wk[-1].isoformat(), "end": today.isoformat()}],
                            f"hrv:{today.isocalendar()[1]}"))

    # 5. Activity level change (7 d vs prior 28 d)
    st7 = f.loc[wk[-1]:today, "steps"].dropna()
    st28 = f.loc[wk[-1] - timedelta(days=28):wk[-1] - timedelta(days=1), "steps"].dropna()
    if len(st7) >= 5 and len(st28) >= 14 and st28.median() > 0:
        rel = st7.mean() / st28.mean() - 1
        if abs(rel) >= 0.25:
            out.append(_ins("activity", "info", "Activity up this week" if rel > 0 else "Activity down this week",
                            f"{st7.mean():,.0f} steps/day vs {st28.mean():,.0f} in the previous 4 weeks "
                            f"({rel:+.0%}).", wk[-1], today, {"mean_7d": float(st7.mean()),
                                                               "mean_prior_28d": float(st28.mean())},
                            "prior 28 days", len(st7) / 7, "moderate",
                            None if rel > 0 else "Short walks after meals are an easy way to add movement.",
                            [{"feature": "steps", "start": (wk[-1] - timedelta(days=28)).isoformat(),
                              "end": today.isoformat()}], f"steps:{today.isocalendar()[1]}"))

    # 6. Recovery trend
    rec = get_scores(db, "recovery", today - timedelta(days=13), today)
    zs = [r["z"] for r in rec if r["z"] is not None]
    if len(zs) >= 10:
        first, last = np.mean(zs[:7]), np.mean(zs[-7:])
        if abs(last - first) >= 0.6:
            out.append(_ins("recovery", "info", "Recovery trending up" if last > first else "Recovery trending down",
                            f"Average recovery z-score {last:+.2f} this week vs {first:+.2f} the week before.",
                            today - timedelta(days=13), today, {"z_this_week": float(last), "z_prev_week": float(first)},
                            "personal baseline", len(zs) / 14, "low",
                            "Open Recovery to see which inputs drove the change.",
                            [{"feature": "recovery", "start": (today - timedelta(days=13)).isoformat(),
                              "end": today.isoformat()}], f"recovery_trend:{today.isocalendar()[1]}"))

    # 7. Long-term trend (90 d) for RHR via Theil–Sen + Mann–Kendall-style Kendall tau
    r90 = f.loc[today - timedelta(days=89):today, "rhr"].dropna()
    if len(r90) >= 45:
        x = np.array([(d - r90.index[0]).days for d in r90.index], float)
        slope, _, lo, hi = sps.theilslopes(r90.to_numpy(), x)
        tau = sps.kendalltau(x, r90.to_numpy())
        per30 = slope * 30
        if tau.pvalue < 0.01 and abs(per30) >= 1.0:
            out.append(_ins("cardio", "info", "Resting heart rate trending " + ("down" if slope < 0 else "up"),
                            f"Over 90 days resting HR changed by about {per30:+.1f} bpm per month "
                            f"(Theil–Sen slope, {len(r90)} nights).", today - timedelta(days=89), today,
                            {"slope_bpm_per_30d": float(per30), "ci_per_30d": [float(lo * 30), float(hi * 30)],
                             "kendall_p": float(tau.pvalue)}, None, len(r90) / 90, "moderate", None,
                            [{"feature": "rhr", "start": (today - timedelta(days=89)).isoformat(),
                              "end": today.isoformat()}], f"rhr_trend90:{today.isoformat()[:7]}"))

    # 8. Missing data
    for feat, label in (("sleep_tst_min", "sleep"), ("rhr", "heart rate"), ("steps", "steps")):
        recent = f.loc[today - timedelta(days=2):today, feat].dropna()
        before = f.loc[today - timedelta(days=30):today - timedelta(days=3), feat].dropna()
        if recent.empty and len(before) >= 10:
            out.append(_ins("data", "notice", f"No {label} data for 3 days",
                            f"VITALIS has no {label} data since {before.index[-1].isoformat()}.",
                            today - timedelta(days=2), today, {"last_date": before.index[-1].isoformat()}, None, 0.0,
                            "high", "Open the companion app on your phone and run a sync, or check the watch is worn.",
                            [{"feature": feat, "start": (today - timedelta(days=30)).isoformat(),
                              "end": today.isoformat()}], f"missing:{feat}:{today.isoformat()}"))

    # 9. Habit associations (FDR-controlled screen of journal habits vs key outcomes)
    habits = [f"j:{r['key']}" for r in db.query("SELECT key FROM journal_habits WHERE active=1 AND value_type IN"
                                                " ('boolean','number','scale','time')")]
    if habits:
        res = scan(db, habits, ["sleep_tst_min", "sleep_efficiency", "rhr", "hrv_night_rmssd"],
                   today - timedelta(days=119), today)
        for r in [r for r in res["results"] if r["survives_fdr"] and abs(r["r"] or 0) >= 0.3][:3]:
            out.append(_ins("habits", "info", f"{r['exposure_label']} is associated with {r['outcome_label'].lower()}",
                            f"Across {r['n']} days, ρ = {r['r']:.2f} (95% CI {r['ci'][0]:.2f}–{r['ci'][1]:.2f}, "
                            f"FDR q = {r['q']:.3f}). This is an association, not proof of cause.",
                            today - timedelta(days=119), today, r, None, None, "low",
                            "Consider testing it with a 4-week experiment in the Experiment Lab.",
                            [{"feature": r["exposure"], "start": (today - timedelta(days=119)).isoformat(),
                              "end": today.isoformat()},
                             {"feature": r["outcome"], "start": (today - timedelta(days=119)).isoformat(),
                              "end": today.isoformat()}],
                            f"habit:{r['exposure']}:{r['outcome']}:{today.isoformat()[:7]}"))
    return _dedupe(out)


def _dedupe(items: list[dict]) -> list[dict]:
    seen, out = set(), []
    topics = {}
    for it in items:
        if it["dedup_key"] in seen:
            continue
        seen.add(it["dedup_key"])
        topic = it["dedup_key"].split(":")[0].replace("_long", "_short").replace("_short", "_dur")
        # contradiction suppression: keep only one insight per topic/period
        if topic in topics:
            continue
        topics[topic] = it
        out.append(it)
    return out


def store(db: Database, items: list[dict]) -> int:
    now = utcnow()
    n = 0
    with db.transaction() as conn:
        for it in items:
            cur = conn.execute(
                "INSERT INTO insights(id, dedup_key, category, severity, title, observation, period_start, period_end,"
                " numbers, baseline, completeness, confidence, action, evidence, algo_version, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(dedup_key) DO UPDATE SET"
                " observation=excluded.observation, numbers=excluded.numbers, period_end=excluded.period_end",
                (it["id"], it["dedup_key"], it["category"], it["severity"], it["title"], it["observation"],
                 it["period_start"], it["period_end"], json.dumps(it["numbers"], default=float), it["baseline"],
                 it["completeness"], it["confidence"], it["action"], json.dumps(it["evidence"]), it["algo_version"], now))
            n += cur.rowcount
    return n


def list_insights(db: Database, since: date | None = None, include_dismissed: bool = False) -> list[dict]:
    q = "SELECT * FROM insights WHERE 1=1"
    p: list = []
    if since:
        q += " AND period_end >= ?"
        p.append(since.isoformat())
    if not include_dismissed:
        q += " AND dismissed=0"
    q += " ORDER BY period_end DESC, CASE severity WHEN 'attention' THEN 0 WHEN 'notice' THEN 1 ELSE 2 END"
    rows = db.query(q, p)
    for r in rows:
        r["numbers"] = json.loads(r["numbers"])
        r["evidence"] = json.loads(r["evidence"])
    return rows


def feature_label(f: str) -> str:
    return feature_info(f)["label"]
