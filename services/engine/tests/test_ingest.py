import io
import json
import zipfile
from datetime import date

from conftest import hc

from vitalis_engine.analytics.data import load_features
from vitalis_engine.analytics.features import compute_features, store_features
from vitalis_engine.ingest import google_health, health_connect, samsung_export, samsung_sdk
from vitalis_engine.ingest.pipeline import Importer, resolve_duplicates
from vitalis_engine.model.metrics import QualityFlag


def _run(db, recs, provider="health_connect", adapter=health_connect.normalize, **kw):
    return Importer(db).run(provider=provider, import_method="test", records=recs, adapter=adapter, **kw)


def test_health_connect_mapping_and_provenance(db):
    recs = [
        hc("HeartRateRecord", id="hr1", startTime="2026-11-10T21:00:00Z", startZoneOffset="+01:00",
           endTime="2026-11-10T21:20:00Z", samples=[{"time": "2026-11-10T21:00:00Z", "bpm": 61},
                                                    {"time": "2026-11-10T21:10:00Z", "bpm": 63}]),
        hc("OxygenSaturationRecord", id="o1", time="2026-11-11T02:00:00Z", zoneOffset="+01:00", percent=95.0),
        hc("BloodPressureRecord", id="bp1", time="2026-11-11T07:00:00Z", zoneOffset="+01:00",
           systolicMmHg=121.0, diastolicMmHg=79.0, recordingMethod="manual_entry"),
        hc("SleepSessionRecord", id="s1", startTime="2026-11-10T22:00:00Z", startZoneOffset="+01:00",
           endTime="2026-11-11T06:00:00Z", stages=[{"startTime": "2026-11-10T22:00:00Z",
                                                    "endTime": "2026-11-11T06:00:00Z", "stage": "light"}]),
        hc("SomeFutureRecord", id="x"),
    ]
    s = _run(db, recs)
    assert s.inserted == 6  # 2 HR + SpO2 + 2 BP + 1 sleep
    assert s.unsupported_types == {"SomeFutureRecord": 1}
    rows = db.query("SELECT metric, value_num, unit, provenance, utc_offset_min, local_date FROM measurements"
                    " ORDER BY metric, start_utc")
    bp = [r for r in rows if r["metric"] == "bp_systolic"][0]
    assert bp["provenance"] == "user_entered" and bp["value_num"] == 121.0 and bp["unit"] == "mmHg"
    hr = [r for r in rows if r["metric"] == "heart_rate"]
    assert [h["value_num"] for h in hr] == [61, 63] and hr[0]["local_date"] == "2026-11-10"
    sl = db.query_one("SELECT local_date, has_stages FROM sleep_sessions")
    assert sl == {"local_date": "2026-11-11", "has_stages": 1}  # dated by wake time
    src = db.query_one("SELECT provider, app_package, device_model FROM sources")
    assert src["app_package"] == "com.sec.android.app.shealth" and src["device_model"] == "SM-R960"
    raw = db.query_one("SELECT count(*) AS n FROM raw_records")
    assert raw["n"] == 5  # raw records retained, including unsupported


def test_reimport_is_idempotent_and_updates_and_deletions(db):
    rec = hc("RestingHeartRateRecord", id="r1", time="2026-11-11T06:00:00Z", zoneOffset="+01:00", bpm=55)
    assert _run(db, [rec]).inserted == 1
    s = _run(db, [rec])
    assert s.inserted == 0 and s.duplicate == 1
    rec2 = dict(rec, bpm=57, lastModified="2026-11-12T00:00:00Z")
    s = _run(db, [rec2])
    assert s.updated == 1
    assert db.query_one("SELECT value_num FROM measurements")["value_num"] == 57
    s = _run(db, [], deletions=[{"id": "r1"}])
    assert s.deleted == 1
    assert db.query_one("SELECT deleted_at FROM measurements")["deleted_at"] is not None


def test_same_batch_id_not_imported_twice(db):
    rec = hc("StepsRecord", id="st", startTime="2026-11-11T10:00:00Z", endTime="2026-11-11T11:00:00Z", count=500)
    _run(db, [rec], batch_id="11111111-2222-3333-4444-555555555555")
    s = _run(db, [rec], batch_id="11111111-2222-3333-4444-555555555555")
    assert s.already_imported and s.received == 0


def test_quality_flags_range_future_and_tz(db):
    recs = [hc("HeartRateRecord", id="h", startTime="2026-11-11T10:00:00Z", endTime="2026-11-11T10:01:00Z",
               samples=[{"time": "2026-11-11T10:00:00Z", "bpm": 300}]),
            hc("OxygenSaturationRecord", id="f", time="2099-01-01T00:00:00Z", zoneOffset="+01:00", percent=97)]
    _run(db, recs)
    rows = {r["metric"]: r["quality_flags"] for r in db.query("SELECT metric, quality_flags FROM measurements")}
    assert rows["heart_rate"] & QualityFlag.OUT_OF_RANGE
    assert rows["heart_rate"] & QualityFlag.TZ_INFERRED  # no startZoneOffset
    assert rows["spo2"] & QualityFlag.FUTURE_TIMESTAMP


def test_malformed_record_rejected_but_raw_kept(db):
    s = _run(db, [hc("OxygenSaturationRecord", id="o", time="2026-11-11T02:00:00Z")])
    assert s.rejected == 1 and s.inserted == 0
    assert db.query_one("SELECT count(*) AS n FROM raw_records")["n"] == 1


def test_cross_provider_dedupe_respects_priority(db):
    hr_hc = hc("HeartRateRecord", id="a", startTime="2026-11-11T10:00:00Z", endTime="2026-11-11T10:01:00Z",
               startZoneOffset="+01:00", samples=[{"time": "2026-11-11T10:00:00Z", "bpm": 70}])
    gh = {"dataType": "heart-rate", "dataPoint": {"heartRate": {"sampleTime": {
        "physicalTime": "2026-11-11T10:00:20Z", "utcOffset": "3600s"}, "beatsPerMinute": "71"}}}
    _run(db, [hr_hc])
    _run(db, [gh], provider="google_health", adapter=google_health.normalize)
    resolve_duplicates(db, "2026-11-11", "2026-11-11")
    rows = {r["provider"]: r["superseded_by"] for r in db.query("SELECT provider, superseded_by FROM measurements")}
    assert rows["health_connect"] is None and rows["google_health"] is not None
    db.set_pref("source_priority", ["google_health", "health_connect"])
    resolve_duplicates(db, "2026-11-11", "2026-11-11")
    rows = {r["provider"]: r["superseded_by"] for r in db.query("SELECT provider, superseded_by FROM measurements")}
    assert rows["health_connect"] is not None and rows["google_health"] is None


def test_steps_not_double_counted_across_providers(db):
    hcs = [hc("StepsAggregateHourly", startTime=f"2026-11-11T{h:02d}:00:00Z", endTime=f"2026-11-11T{h + 1:02d}:00:00Z",
              startZoneOffset="+01:00", value=1000) for h in range(8, 12)]
    gh = [{"dataType": "steps", "dataPoint": {"steps": {"interval": {
        "startTime": f"2026-11-11T{h:02d}:00:00Z", "endTime": f"2026-11-11T{h:02d}:59:00Z",
        "startUtcOffset": "3600s", "endUtcOffset": "3600s"}, "count": "990"}}} for h in range(8, 12)]
    _run(db, hcs)
    _run(db, gh, provider="google_health", adapter=google_health.normalize)
    res = compute_features(db, date(2026, 11, 11), date(2026, 11, 11))
    store_features(db, res, date(2026, 11, 11), date(2026, 11, 11))
    f = load_features(db, ["steps"], date(2026, 11, 11), date(2026, 11, 11))
    assert f.iloc[0]["steps"] == 4000  # one provider chosen by priority, never 7960


def test_google_health_shapes_from_reference_docs():
    sleep = {"dataType": "sleep", "dataPoint": {"name": "users/me/dataTypes/sleep/dataPoints/abc", "dataSource": {
        "recordingMethod": "PASSIVELY_MEASURED", "device": {"formFactor": "WATCH", "displayName": "Pixel Watch 3"}},
        "sleep": {"interval": {"startTime": "2026-11-10T22:00:00Z", "startUtcOffset": "3600s",
                               "endTime": "2026-11-11T06:00:00Z", "endUtcOffset": "3600s"},
                  "stages": [{"startTime": "2026-11-10T22:00:00Z", "endTime": "2026-11-10T23:00:00Z", "type": "DEEP"},
                             {"startTime": "2026-11-10T23:00:00Z", "endTime": "2026-11-10T23:10:00Z",
                              "type": "RESTLESS"}],
                  "summary": {"minutesAsleep": "420"}}}}
    n = google_health.normalize(sleep)
    assert n.provider_record_id.endswith("abc") and n.sleeps[0].stages[0].stage == "deep"
    assert n.sleeps[0].stages[1].stage == "awake" and n.source.device_model == "Pixel Watch 3"
    hrv = google_health.normalize({"dataType": "daily-heart-rate-variability", "dataPoint": {
        "dailyHeartRateVariability": {"date": {"year": 2026, "month": 11, "day": 11},
                                      "averageHeartRateVariabilityMilliseconds": 41.5, "entropy": 2.1}}})
    assert hrv.measurements[0].metric == "hrv_rmssd_daily" and hrv.measurements[0].value == 41.5
    assert "entropy" in hrv.unmapped_fields
    dist = google_health.normalize({"dataType": "distance", "dataPoint": {"distance": {"interval": {
        "startTime": "2026-11-11T10:00:00Z", "endTime": "2026-11-11T10:01:00Z"}, "millimeters": "123000"}}})
    assert dist.measurements[0].value == 123.0 and dist.measurements[0].orig_unit == "mm"
    assert google_health.normalize({"dataType": "electrocardiogram", "dataPoint": {}}).unsupported
    flt = google_health.list_filter("steps", google_health.parse_instant("2026-11-01T00:00:00Z"),
                                    google_health.parse_instant("2026-11-02T00:00:00Z"))
    assert flt == 'steps.interval.start_time >= "2026-11-01T00:00:00Z" AND steps.interval.start_time < "2026-11-02T00:00:00Z"'
    assert google_health.list_filter("sleep", google_health.parse_instant("2026-11-01T00:00:00Z"),
                                     google_health.parse_instant("2026-11-02T00:00:00Z")).startswith(
        "sleep.interval.end_time >=")
    assert google_health.list_filter("heart-rate", google_health.parse_instant("2026-11-01T00:00:00Z"),
                                     google_health.parse_instant("2026-11-02T00:00:00Z")).startswith(
        "heart_rate.sample_time.physical_time")


def test_samsung_sdk_mapping_and_unmapped_fields():
    n = samsung_sdk.normalize({"type": "SAMSUNG_SDK.SLEEP", "uid": "u1", "startTime": "2026-11-10T22:00:00Z",
                               "endTime": "2026-11-11T06:00:00Z", "zoneOffset": "+01:00",
                               "fields": {"SLEEP_SCORE": 81, "MYSTERY": 3},
                               "sessions": [{"startTime": "2026-11-10T22:00:00Z", "endTime": "2026-11-11T06:00:00Z",
                                             "stages": [{"startTime": "2026-11-10T22:00:00Z",
                                                         "endTime": "2026-11-10T23:00:00Z", "stage": "DEEP"}]}]})
    assert n.sleeps[0].stages[0].stage == "deep"
    assert n.measurements[0].metric == "sleep_score" and n.measurements[0].provenance == "imported"
    assert n.unmapped_fields == ["MYSTERY"]
    n = samsung_sdk.normalize({"type": "SAMSUNG_SDK.BLOOD_OXYGEN", "uid": "u2", "startTime": "2026-11-11T02:00:00Z",
                               "fields": {"OXYGEN_SATURATION": 96}})
    assert n.measurements[0].metric == "spo2" and n.measurements[0].meta["field_verified"] is True
    assert samsung_sdk.normalize({"type": "SAMSUNG_SDK.STRESS", "uid": "u3",
                                  "startTime": "2026-11-11T02:00:00Z"}).unsupported


def _zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, t in files.items():
            z.writestr(f"samsunghealth_x/{n}", t)
    return buf.getvalue()


def test_samsung_export_preview_and_import_with_schema_variants(db, tmp_path):
    hr = ("com.samsung.shealth.tracker.heart_rate,6315005,5\n"
          "com.samsung.health.heart_rate.start_time,com.samsung.health.heart_rate.heart_rate,"
          "com.samsung.health.heart_rate.time_offset,com.samsung.health.heart_rate.datauuid,\n"
          "2026-11-11 06:00:00.000,58,UTC+0100,uuid-1,\n2026-11-11 06:10:00.000,60,UTC+0100,uuid-2,\n")
    # newer variant: no metadata line, different column prefix
    spo2 = ("start_time,spo2,time_offset,datauuid\n2026-11-11 02:00:00.000,96,UTC+0100,uuid-3\n")
    stages = ("com.samsung.health.sleep_stage,1,1\nstart_time,end_time,stage,sleep_id,time_offset,datauuid\n"
              "2026-11-10 22:00:00.000,2026-11-10 23:00:00.000,40003,s1,UTC+0100,st1\n"
              "2026-11-10 23:00:00.000,2026-11-11 05:00:00.000,40002,s1,UTC+0100,st2\n")
    sleep = ("com.samsung.shealth.sleep,1,1\ncom.samsung.health.sleep.start_time,com.samsung.health.sleep.end_time,"
             "com.samsung.health.sleep.time_offset,com.samsung.health.sleep.datauuid,sleep_score\n"
             "2026-11-10 22:00:00.000,2026-11-11 05:00:00.000,UTC+0100,s1,77\n")
    zp = tmp_path / "export.zip"
    zp.write_bytes(_zip({"com.samsung.shealth.tracker.heart_rate.20261112.csv": hr,
                         "com.samsung.shealth.tracker.oxygen_saturation.20261112.csv": spo2,
                         "com.samsung.health.sleep_stage.20261112.csv": stages,
                         "com.samsung.shealth.sleep.20261112.csv": sleep,
                         "com.samsung.shealth.unknown_table.20261112.csv": "a,b\n1,2\n"}))
    pv = samsung_export.preview(str(zp))
    tables = {t["table"]: t for t in pv["tables"]}
    assert tables["heart_rate"]["rows"] == 2 and tables["spo2"]["rows"] == 1
    assert tables["sleep_stage"]["stage_codes"] == {"40003": 1, "40002": 1}
    assert pv["unmatched_files"][0]["file"].startswith("com.samsung.shealth.unknown_table")
    s = Importer(db).run(provider="samsung_export", import_method="file_import",
                         records=samsung_export.iter_records(str(zp)), adapter=samsung_export.normalize)
    assert s.rejected == 0 and s.inserted >= 5
    res = compute_features(db, date(2026, 11, 11), date(2026, 11, 11))
    vals = {k[1]: v[0] for k, v in res["values"].items()}
    assert vals["sleep_tst_min"] == 420.0 and vals["sleep_deep_min"] == 60.0  # stage rows attached to the night
    assert vals["sleep_score_imported"] == 77
