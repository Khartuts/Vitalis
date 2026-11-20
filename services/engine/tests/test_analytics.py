import math
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest
from conftest import hc

from vitalis_engine.analytics import experiments, stats
from vitalis_engine.analytics.baseline import compute
from vitalis_engine.analytics.data import SleepEpisode, load_sleep_episodes
from vitalis_engine.analytics.features import compute_features, sleep_metrics
from vitalis_engine.analytics.hrv import time_domain
from vitalis_engine.analytics.scores import recovery_for_day
from vitalis_engine.ingest import health_connect
from vitalis_engine.ingest.pipeline import Importer

UTC = timezone.utc


def T(s):
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


def test_sleep_metrics_from_stages():
    ep = SleepEpisode(T("2026-11-10T22:00:00"), T("2026-11-11T06:00:00"), 60, date(2026, 11, 11), ["r"], ["hc"], [
        ("awake", T("2026-11-10T22:00:00"), T("2026-11-10T22:20:00")),   # latency 20
        ("light", T("2026-11-10T22:20:00"), T("2026-11-11T00:00:00")),
        ("deep", T("2026-11-11T00:00:00"), T("2026-11-11T01:00:00")),
        ("awake", T("2026-11-11T01:00:00"), T("2026-11-11T01:01:00")),   # 1 min: not an awakening
        ("light", T("2026-11-11T01:01:00"), T("2026-11-11T03:00:00")),
        ("awake", T("2026-11-11T03:00:00"), T("2026-11-11T03:10:00")),   # 10 min: awakening
        ("rem", T("2026-11-11T03:10:00"), T("2026-11-11T06:00:00")),
    ], has_stages=True)
    m = sleep_metrics(ep, "Europe/Zagreb")
    assert m["tib"] == 480 and m["tst"] == pytest.approx(480 - 31)
    assert m["latency"] == 20 and m["waso"] == 11 and m["awakenings"] == 1
    assert m["deep"] == 60 and m["efficiency"] == pytest.approx(100 * 449 / 480)
    assert m["bedtime"] == -60 and m["wake"] == 7 * 60  # 23:00 local and 07:00 local (UTC+1)


def test_no_stages_means_no_inferred_stage_metrics():
    ep = SleepEpisode(T("2026-11-10T22:00:00"), T("2026-11-11T05:00:00"), 60, date(2026, 11, 11), ["r"], ["x"], [],
                      has_stages=False)
    m = sleep_metrics(ep, "Europe/Zagreb")
    assert m["tst"] == 420 and "efficiency" not in m and "deep" not in m and "latency" not in m


def test_session_merging_naps_and_dst_night(db):
    # DST fall-back night 2026-10-24 -> 25 split in two sessions (gap 30 min) plus an afternoon nap
    recs = [
        hc("SleepSessionRecord", id="a", startTime="2026-10-24T21:00:00Z", startZoneOffset="+02:00",
           endTime="2026-10-25T01:00:00Z", stages=[]),
        hc("SleepSessionRecord", id="b", startTime="2026-10-25T01:30:00Z", startZoneOffset="+01:00",
           endTime="2026-10-25T05:30:00Z", stages=[]),
        hc("SleepSessionRecord", id="nap", startTime="2026-10-25T13:00:00Z", startZoneOffset="+01:00",
           endTime="2026-10-25T13:40:00Z", stages=[]),
    ]
    Importer(db).run(provider="health_connect", import_method="t", records=recs, adapter=health_connect.normalize)
    eps = load_sleep_episodes(db, date(2026, 10, 25), date(2026, 10, 25))
    assert len(eps) == 2 and len(eps[0].record_ids) == 2  # two sessions merged into one night + a nap
    res = compute_features(db, date(2026, 10, 25), date(2026, 10, 25))
    v = {k[1]: x[0] for k, x in res["values"].items()}
    assert v["sleep_tst_min"] == 8.5 * 60  # no stages: episode duration including the 30-min gap
    assert v["nap_min"] == 40
    assert v["sleep_bedtime_min"] == pytest.approx(-60)    # 21:00Z = 23:00 CEST
    assert v["sleep_wake_min"] == pytest.approx(6.5 * 60)  # 05:30Z = 06:30 CET after the change


def test_single_session_across_dst_without_end_offset(db):
    rec = hc("SleepSessionRecord", id="x", startTime="2026-10-24T21:00:00Z", startZoneOffset="+02:00",
             endTime="2026-10-25T05:30:00Z", stages=[])
    Importer(db).run(provider="health_connect", import_method="t", records=[rec], adapter=health_connect.normalize)
    res = compute_features(db, date(2026, 10, 25), date(2026, 10, 25))
    v = {k[1]: x[0] for k, x in res["values"].items()}
    assert v["sleep_wake_min"] == pytest.approx(6.5 * 60) and v["sleep_tst_min"] == 510  # elapsed time is 8.5 h


def test_rhr_from_sleep_hr_and_hr_zones(db):
    db.set_pref("hr_max", 200)
    samples = [{"time": (T("2026-11-10T22:00:00") + timedelta(minutes=10 * i)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "bpm": 60 - (5 if 10 <= i < 14 else 0)} for i in range(48)]
    ex = [{"time": (T("2026-11-11T17:00:00") + timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:%SZ"), "bpm": 150}
          for i in range(31)]
    recs = [hc("SleepSessionRecord", id="s", startTime="2026-11-10T22:00:00Z", startZoneOffset="+01:00",
               endTime="2026-11-11T06:00:00Z", stages=[]),
            hc("HeartRateRecord", id="h1", startTime="2026-11-10T22:00:00Z", startZoneOffset="+01:00",
               endTime="2026-11-11T06:00:00Z", samples=samples),
            hc("HeartRateRecord", id="h2", startTime="2026-11-11T17:00:00Z", startZoneOffset="+01:00",
               endTime="2026-11-11T17:31:00Z", samples=ex)]
    Importer(db).run(provider="health_connect", import_method="t", records=recs, adapter=health_connect.normalize)
    res = compute_features(db, date(2026, 11, 11), date(2026, 11, 11))
    v = {k[1]: x[0] for k, x in res["values"].items()}
    assert v["rhr_sleep_est"] == pytest.approx(55.0)  # lowest 30-min rolling mean
    assert v["rhr"] == v["rhr_sleep_est"]
    assert v["hr_z3_min"] == pytest.approx(30, abs=1.5)  # 150/200 = 75 % HRmax, 1-min samples
    assert v["trimp"] == pytest.approx(3 * v["hr_z3_min"])


def test_baseline_status_thresholds():
    idx = [date(2026, 1, 1) + timedelta(days=i) for i in range(40)]
    s = pd.Series(np.linspace(50, 60, 40), index=idx)
    assert compute(s, "rhr", idx[10], 60, 14, 28).status == "insufficient"
    assert compute(s, "rhr", idx[20], 60, 14, 28).status == "provisional"
    b = compute(s, "rhr", idx[35], 60, 14, 28)
    assert b.status == "stable" and b.n == 35 and b.z(b.median) == 0
    flat = pd.Series([55.0] * 30, index=idx[:30])
    assert compute(flat, "rhr", idx[30]).robust_sd == 1.0  # SD floor prevents explosive z-scores


def test_recovery_requires_physiology_and_sleep():
    idx = [date(2026, 1, 1) + timedelta(days=i) for i in range(40)]
    f = pd.DataFrame({"hrv_night_rmssd": np.nan, "rhr": 55.0, "sleep_tst_min": np.nan,
                      "sleep_bedtime_min": -30.0, "trimp": 50.0}, index=idx)
    r = recovery_for_day(f, idx[-1], 450, 60, 14, 28)
    assert r["value"] is None and r["category"] == "insufficient_data"
    f["sleep_tst_min"] = 450.0
    f.loc[idx[-1], "rhr"] = 60.0  # elevated
    r = recovery_for_day(f, idx[-1], 450, 60, 14, 28)
    assert r["value"] < 50 and r["category"] in ("below_usual", "well_below_usual")
    assert sum(u["contribution"] for u in r["detail"]["inputs_used"]) == pytest.approx(r["z"])
    assert any(m["component"] == "hrv" for m in r["detail"]["inputs_missing"])


def test_correlation_ci_covers_truth_and_small_samples():
    rng = np.random.default_rng(1)
    x = rng.normal(size=200)
    y = 0.5 * x + rng.normal(scale=math.sqrt(1 - 0.25), size=200)
    r = stats.correlate(pd.Series(x), pd.Series(y), "pearson")
    assert r["ci"][0] < 0.5 < r["ci"][1] and r["verdict"] == "association_detected"
    small = stats.correlate(pd.Series(x[:8]), pd.Series(y[:8]))
    assert small["verdict"] == "insufficient_data" and small["r"] is None
    noise = stats.correlate(pd.Series(rng.normal(size=60)), pd.Series(rng.normal(size=60)))
    assert noise["verdict"] in ("inconclusive", "association_detected")


def test_effective_n_shrinks_with_autocorrelation():
    t = np.arange(100, dtype=float)
    x = np.sin(t / 10)
    y = np.cos(t / 10)
    assert stats.effective_n(x, y) < 50


def test_benjamini_hochberg_reference_values():
    q = stats.benjamini_hochberg([0.01, 0.04, 0.03, 0.005])
    assert q == pytest.approx([0.02, 0.04, 0.04, 0.02])


def test_group_comparison_and_hedges_g():
    rng = np.random.default_rng(2)
    a = rng.normal(10, 2, 40)
    b = rng.normal(8, 2, 40)
    v = pd.Series(np.concatenate([a, b]))
    m = pd.Series([True] * 40 + [False] * 40)
    r = stats.compare_groups(v, m)
    assert r["diff_mean_ci"][0] > 0 and 0.6 < r["hedges_g"] < 1.6
    r = stats.compare_groups(v[:6], m[:6])
    assert r["verdict"] == "insufficient_data"


def test_time_condition_parsing():
    s = pd.Series([14 * 60.0, 15 * 60 + 30, np.nan])
    out = stats.apply_condition(s, {"op": "after", "value": "15:00"})
    assert out.iloc[0] == False and out.iloc[1] == True and pd.isna(out.iloc[2])  # noqa: E712


def test_hrv_only_from_beat_intervals():
    ibi = [1000, 1020, 990, 1010] * 30
    r = time_domain(ibi)
    diffs = np.diff(ibi)
    assert r["status"] == "ok" and r["rmssd_ms"] == pytest.approx(float(np.sqrt(np.mean(diffs ** 2))))
    noisy = [800, 2500, 200, 1600] * 30
    assert time_domain(noisy)["status"] == "insufficient_quality"


def test_experiment_schedule_balanced_and_reproducible():
    s1 = experiments.make_schedule(date(2026, 1, 1), 7, 28, "randomized_blocks", 2, seed=5)
    s2 = experiments.make_schedule(date(2026, 1, 1), 7, 28, "randomized_blocks", 2, seed=5)
    assert s1 == s2
    vals = list(s1.values())
    assert vals.count("baseline") == 7 and vals.count("intervention") == vals.count("control") == 14
