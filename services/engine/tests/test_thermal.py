"""Body temperature and bedroom temperature (thermal.v1-exploratory)."""
from datetime import date, timedelta

from conftest import END, hc

from vitalis_engine.analytics import insights, thermal
from vitalis_engine.ingest import health_connect


def test_skin_temperature_absolute_only_with_source_baseline():
    rec = hc("SkinTemperatureRecord", id="sk1", startTime="2026-11-18T22:00:00Z", endTime="2026-11-19T06:00:00Z",
             startZoneOffset="+01:00", endZoneOffset="+01:00", baselineC=33.4, location="wrist",
             deltas=[{"time": "2026-11-19T02:00:00Z", "deltaC": 0.3}])
    out = health_connect.normalize(rec)
    by = {m.metric: m for m in out.measurements}
    assert by["skin_temp_delta"].value == 0.3
    assert abs(by["skin_temperature"].value - 33.7) < 1e-9 and by["skin_temperature"].provenance == "calculated"
    rec.pop("baselineC")
    assert [m.metric for m in health_connect.normalize(rec).measurements] == ["skin_temp_delta"]


def test_summary_finds_warm_nights_and_room_effect(demo_db):
    start = END - timedelta(days=89)
    s = thermal.summary(demo_db, start, END)
    assert s["has_skin"] and s["nights_with_bedroom_temp"] > 60
    # Built-in demo effect: a warmer bedroom -> warmer overnight skin.
    c = s["correlations"]["bedroom_vs_skin"]
    assert c["r"] > 0.3 and c["ci"][0] > 0
    # The demo illness episode (around day 55) shows up as warmer-than-usual nights.
    ill = {(start + timedelta(days=i)).isoformat() for i in range(52, 62)}
    assert set(s["elevated_nights"]) & ill
    assert all(n["bedroom_source"] in (None, "journal") for n in s["nights"])


def test_suggestion_is_bounded_explained_and_respects_limits(demo_db):
    sug = thermal.suggestion(demo_db, END)
    lo, hi = sug["limits"]
    assert lo <= sug["suggested_c"] <= hi and sug["reasons"] and sug["limitations"]
    # Built-in demo effect: warmer than 19 degC fragments sleep -> the personal band is a cool one.
    assert sug["basis"] == "personal" and sug["suggested_c"] <= 19.0
    assert sug["personal_model"]["difference_ci"][0] > 0
    demo_db.set_pref("thermal_max_c", 17.0)
    try:
        assert thermal.suggestion(demo_db, END)["suggested_c"] <= 17.0
    finally:
        demo_db.set_pref("thermal_max_c", None)


def test_general_guidance_without_bedroom_data(db):
    sug = thermal.suggestion(db, date(2026, 11, 19))
    assert sug["basis"] == "general" and sug["suggested_c"] == 18.0
    assert any("16–19 °C" in r for r in sug["reasons"])


def test_warm_skin_insight_during_demo_illness(demo_db):
    start = END - timedelta(days=89)
    found = []
    for i in range(54, 62):
        items = insights.generate(demo_db, start + timedelta(days=i))
        found += [x for x in items if x["dedup_key"].startswith("skin_warm:")]
    assert found, "no warm-skin insight around the demo illness"
    assert "not a fever measurement" in found[0]["action"]
