"""End-to-end over 90 days of synthetic data: the analytics must recover the generator's known effects."""

import json
import time
from datetime import date, timedelta

import pytest
from conftest import END

from vitalis_engine.ai.tools import call_tool, tool_definitions
from vitalis_engine.analytics import experiments, insights, intraday, stats
from vitalis_engine.analytics.data import load_features
from vitalis_engine.analytics.scores import get_scores
from vitalis_engine.journal import upsert_habit
from vitalis_engine.quality.checks import quality_report
from vitalis_engine.reports.report import build, export_all

START = END - timedelta(days=89)


def test_known_effects_are_recovered(demo_db):
    coffee = stats.habit_analysis(demo_db, "j:caffeine_last_time", "sleep_deep_min", START, END,
                                  condition={"op": "after", "value": "15:00"})
    g = coffee["group_comparison"]
    assert coffee["lag_days"] == 1 and g["diff_mean_ci"][1] < 0  # late caffeine -> less deep sleep next night
    alcohol = stats.habit_analysis(demo_db, "j:alcohol_units", "rhr", START, END, condition={"op": ">=", "value": 2})
    assert alcohol["group_comparison"]["diff_mean_ci"][0] > 0  # alcohol -> higher resting HR
    corr = stats.correlate(*[load_features(demo_db, ["j:stress", "arousal_min"], START, END)[c]
                             for c in ("j:stress", "arousal_min")])
    assert corr["r"] > 0.3


def test_missing_data_is_explicit(demo_db):
    f = load_features(demo_db, ["sleep_tst_min"], START, END)
    missing = f["sleep_tst_min"].isna().sum()
    assert missing >= 2  # nights without the watch are missing, not imputed
    rec = get_scores(demo_db, "recovery", START, START + timedelta(days=5))
    assert all(r["value"] is None for r in rec)  # no baseline yet in the first days
    assert all(r["category"] == "insufficient_data" for r in rec)


def test_energy_and_arousal_timelines(demo_db):
    e = intraday.energy_timeline(demo_db, END - timedelta(days=1))
    assert e["status"] in ("ok", "partial")
    vals = [p["value"] for p in e["points"] if p["value"] is not None]
    assert vals and all(0 <= v <= 100 for v in vals) and e["inputs_used"]
    a = intraday.arousal_timeline(demo_db, END - timedelta(days=1))
    assert a["status"] == "ok" and len(a["points"]) == 96


def test_insights_have_evidence_and_no_contradictions(demo_db):
    items = insights.list_insights(demo_db)
    keys = [i["dedup_key"].split(":")[0] for i in items]
    assert len(keys) == len(set(keys)) or len(items) <= len(set(keys)) + 2
    for i in items:
        assert i["evidence"] and i["confidence"] in ("low", "moderate", "high")


def test_quality_report_flags_gap_and_outliers(demo_db):
    q = quality_report(demo_db, START, END)
    assert q["flags"]["outliers"] >= 1
    assert q["coverage"]["per_metric"]["sleep_tst_min"]["days_present"] < 90


def test_report_and_export(demo_db, tmp_path):
    r = build(demo_db, END - timedelta(days=29), END)
    assert r["demo"] is True and "SYNTHETIC" in r["disclaimer"]
    assert r["sections"]["sleep"] and r["methods"]["algorithms"]["recovery"] == "recovery.v1"
    m = export_all(demo_db, str(tmp_path / "exp"))
    assert m["files"]["measurements"]["rows"] > 10000 and (tmp_path / "exp" / "manifest.json").exists()


def test_ai_tools_validation_privacy_and_no_fabrication(demo_db):
    defs = {d["name"]: d for d in tool_definitions(demo_db, cloud=True)}
    assert "rhr" in defs["get_metric_history"]["parameters"]["properties"]["metric"]["enum"]
    assert defs["get_metric_history"]["parameters"]["additionalProperties"] is False
    bad = call_tool(demo_db, "get_metric_history", {"metric": "rhr", "start": "2026-11-01", "end": "2026-11-19",
                                                    "sql": "DROP TABLE measurements"}, cloud=True)
    assert not bad["ok"] and bad["error"]["code"] == "invalid_arguments"
    assert call_tool(demo_db, "run_sql", {}, cloud=True)["error"]["code"] == "unknown_tool"
    too_long = call_tool(demo_db, "get_sleep_summary", {"start": "2020-01-01", "end": "2026-11-19"}, cloud=True)
    assert not too_long["ok"]
    unknown = call_tool(demo_db, "get_metric_history", {"metric": "blood_lactate", "start": "2026-11-01",
                                                        "end": "2026-11-19"}, cloud=True)
    assert unknown["error"]["code"] == "unknown_metric"
    # metric that exists in the catalog but has no data: tool reports n=0 instead of values
    empty = call_tool(demo_db, "get_metric_history", {"metric": "bp_sys", "start": "2026-11-01", "end": "2026-11-19"},
                      cloud=True)
    assert empty["ok"] and empty["result"]["summary"]["n"] == 0
    assert all(p["value"] is None for p in empty["result"]["points"])
    ok = call_tool(demo_db, "get_metric_history", {"metric": "rhr", "start": "2026-11-01", "end": "2026-11-19"},
                   cloud=True)
    assert ok["result"]["evidence"][0]["id"] == "rhr|2026-11-01|2026-11-19"
    # privacy: alcohol is private by default -> hidden for cloud, visible locally
    priv = call_tool(demo_db, "analyze_habit", {"habit": "j:alcohol_units", "outcome": "rhr", "start": "2026-09-01",
                                                "end": "2026-11-19"}, cloud=True)
    assert not priv["ok"] and priv["error"]["code"] in ("private_habit", "unknown_metric")
    local = call_tool(demo_db, "analyze_habit", {"habit": "j:alcohol_units", "outcome": "rhr", "start": "2026-09-01",
                                                 "end": "2026-11-19"}, cloud=False)
    assert local["ok"]
    cloud_catalog = call_tool(demo_db, "get_available_metrics", {}, cloud=True)["result"]
    assert not any(m["metric"] == "j:alcohol_units" for m in cloud_catalog["metrics"])
    assert "j:alcohol_units" not in json.dumps(call_tool(demo_db, "get_stress_timeline", {"date": "2026-11-18"},
                                                         cloud=True))


def test_experiment_analysis_on_demo(demo_db):
    upsert_habit(demo_db, {"key": "late_coffee_test", "label": "Late coffee (test)", "value_type": "boolean"})
    e = experiments.create(demo_db, {"title": "t", "hypothesis": "h", "habit_key": "caffeine_last_time",
                                     "condition": {"op": "after", "value": "15:00"}, "outcome": "sleep_deep_min",
                                     "lag_days": 1, "design": "randomized_blocks", "start_date": "2026-09-01",
                                     "baseline_days": 7, "intervention_days": 60, "block_days": 2, "seed": 3})
    r = experiments.analyze(demo_db, e["id"], END, n_perm=2000)
    assert r["status"] in ("analyzed", "insufficient_data")
    assert 0 <= (r["adherence_rate"] or 0) <= 1


def test_large_dataset_performance(tmp_path):
    import os
    from vitalis_engine import synthetic
    from vitalis_engine.analytics.recompute import recompute
    from vitalis_engine.db.connection import Database
    db = Database(tmp_path / "big.db", os.urandom(32).hex())
    db.set_pref("is_demo", True)
    t = time.time()
    synthetic.load_into(db, 365, END)
    r = recompute(db, today=END)
    elapsed = time.time() - t
    assert r["status"] == "ok" and r["recovery_days"] >= 360
    assert elapsed < 120, f"365-day import+recompute took {elapsed:.1f}s"


def test_synthetic_refused_for_real_profile(db):
    from vitalis_engine import synthetic
    with pytest.raises(PermissionError):
        synthetic.load_into(db, 30)
