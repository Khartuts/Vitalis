"""Google Health API v4 adapter (users.dataTypes.dataPoints). Shapes from the v4 REST reference.

Each raw record is {"dataType": "<hyphenated type>", "dataPoint": <DataPoint JSON>}.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, time, timedelta, timezone

from ..model.canonical import (CanonExercise, CanonMeasurement, CanonSleep, CanonStage, NormalizedRecord,
                               SourceInfo)
from ..model.metrics import Provenance
from ..timeutil import parse_instant, parse_offset_minutes

PROVIDER = "google_health"

# Data types requested by VITALIS (read-only scopes) and the union field carrying their payload.
SUPPORTED_TYPES = {
    "heart-rate": "heartRate", "daily-resting-heart-rate": "dailyRestingHeartRate",
    "daily-heart-rate-variability": "dailyHeartRateVariability", "heart-rate-variability": "heartRateVariability",
    "oxygen-saturation": "oxygenSaturation", "daily-oxygen-saturation": "dailyOxygenSaturation",
    "respiratory-rate-sleep-summary": "respiratoryRateSleepSummary", "sleep": "sleep", "steps": "steps",
    "distance": "distance", "active-energy-burned": "activeEnergyBurned", "floors": "floors",
    "exercise": "exercise", "weight": "weight", "body-fat": "bodyFat", "vo2-max": "vo2Max",
}

SCOPES_READONLY = [
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
    "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
    "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
]

_STAGE = {"AWAKE": "awake", "LIGHT": "light", "DEEP": "deep", "REM": "rem", "ASLEEP": "asleep",
          "RESTLESS": "awake", "SLEEP_STAGE_TYPE_UNSPECIFIED": "unknown"}


def _source(dp: dict) -> SourceInfo:
    ds = dp.get("dataSource") or {}
    dev = ds.get("device") or {}
    app = ds.get("application") or {}
    return SourceInfo(PROVIDER, app.get("packageName") or ds.get("platform"), dev.get("formFactor"),
                      dev.get("manufacturer"), dev.get("displayName"))


def _prov(dp: dict) -> str:
    rm = (dp.get("dataSource") or {}).get("recordingMethod")
    if rm == "MANUAL":
        return Provenance.USER_ENTERED
    if rm == "DERIVED":
        return Provenance.IMPORTED
    return Provenance.MEASURED


def _sample_time(obj: dict) -> tuple[datetime, int | None]:
    st = obj["sampleTime"]
    return parse_instant(st["physicalTime"]), parse_offset_minutes(st.get("utcOffset"))


def _interval(obj: dict) -> tuple[datetime, datetime, int | None]:
    iv = obj["interval"]
    return parse_instant(iv["startTime"]), parse_instant(iv["endTime"]), parse_offset_minutes(iv.get("startUtcOffset"))


def _date_noon(obj: dict) -> datetime:
    d = obj["date"]
    # Daily summaries carry a civil date only; anchor at 12:00 UTC so the local date is preserved.
    return datetime.combine(datetime(d["year"], d["month"], d["day"]).date(), time(12), tzinfo=timezone.utc)


def _num(v) -> float | None:
    return None if v is None else float(v)


def normalize(rec: dict) -> NormalizedRecord:
    dtype = str(rec.get("dataType", "unknown"))
    dp = rec.get("dataPoint") or {}
    name = dp.get("name") or None
    pid = name or "gh:" + hashlib.sha256(json.dumps(dp, sort_keys=True).encode()).hexdigest()[:32]
    out = NormalizedRecord(f"google_health.{dtype}", pid, _source(dp))
    field = SUPPORTED_TYPES.get(dtype)
    if field is None or field not in dp:
        out.unsupported = True
        return out
    body = dp[field]
    prov = _prov(dp)
    M = out.measurements

    if dtype == "heart-rate":
        t, off = _sample_time(body)
        M.append(CanonMeasurement("heart_rate", float(body["beatsPerMinute"]), "bpm", t, None, off, pid,
                                  provenance=prov, meta={k: v for k, v in (body.get("metadata") or {}).items()}))
    elif dtype == "daily-resting-heart-rate":
        M.append(CanonMeasurement("resting_heart_rate", float(body["beatsPerMinute"]), "bpm", _date_noon(body), None,
                                  None, pid, provenance=Provenance.IMPORTED,
                                  meta={"daily": True, **(body.get("dailyRestingHeartRateMetadata") or {})}))
    elif dtype == "daily-heart-rate-variability":
        t = _date_noon(body)
        v = _num(body.get("averageHeartRateVariabilityMilliseconds"))
        if v is not None:
            M.append(CanonMeasurement("hrv_rmssd_daily", v, "ms", t, None, None, pid, sub_key="avg",
                                      provenance=Provenance.IMPORTED,
                                      meta={"daily": True, "method": "Google Health average nightly RMSSD"}))
        out.unmapped_fields = [k for k in ("entropy", "deepSleepRootMeanSquareOfSuccessiveDifferencesMilliseconds",
                                           "nonRemHeartRateBeatsPerMinute") if k in body]
    elif dtype == "heart-rate-variability":
        t, off = _sample_time(body)
        if body.get("rootMeanSquareOfSuccessiveDifferencesMilliseconds") is not None:
            M.append(CanonMeasurement("hrv_rmssd", float(body["rootMeanSquareOfSuccessiveDifferencesMilliseconds"]),
                                      "ms", t, None, off, pid, sub_key="rmssd", provenance=prov,
                                      meta={"method": "Google Health RMSSD"}))
        if body.get("standardDeviationMilliseconds") is not None:
            M.append(CanonMeasurement("hrv_sdnn", float(body["standardDeviationMilliseconds"]), "ms", t, None, off,
                                      pid, sub_key="sdnn", provenance=prov, meta={"method": "Google Health SDNN"}))
    elif dtype == "oxygen-saturation":
        t, off = _sample_time(body)
        M.append(CanonMeasurement("spo2", float(body["percentage"]), "%", t, None, off, pid, provenance=prov))
    elif dtype == "daily-oxygen-saturation":
        M.append(CanonMeasurement("spo2_daily_avg", float(body["averagePercentage"]), "%", _date_noon(body), None,
                                  None, pid, provenance=Provenance.IMPORTED,
                                  meta={"lower": body.get("lowerBoundPercentage"),
                                        "upper": body.get("upperBoundPercentage")}))
    elif dtype == "respiratory-rate-sleep-summary":
        t, off = _sample_time(body)
        full = body.get("fullSleepStats") or {}
        rate = full.get("breathsPerMinute")
        if rate is None:
            out.error = "no fullSleepStats.breathsPerMinute"
        else:
            M.append(CanonMeasurement("respiratory_rate", float(rate), "br/min", t, None, off, pid, provenance=prov,
                                      meta={"context": "sleep_summary"}))
    elif dtype == "steps":
        s, e, off = _interval(body)
        M.append(CanonMeasurement("steps", float(body["count"]), "count", s, e, off, pid, provenance=prov))
    elif dtype == "floors":
        s, e, off = _interval(body)
        M.append(CanonMeasurement("floors", float(body["count"]), "count", s, e, off, pid, provenance=prov))
    elif dtype == "distance":
        s, e, off = _interval(body)
        M.append(CanonMeasurement("distance", float(body["millimeters"]) / 1000.0, "m", s, e, off, pid,
                                  orig_value=body["millimeters"], orig_unit="mm", provenance=prov,
                                  quality_flags=8))
    elif dtype == "active-energy-burned":
        s, e, off = _interval(body)
        M.append(CanonMeasurement("active_energy", float(body["kcal"]), "kcal", s, e, off, pid, provenance=prov))
    elif dtype == "weight":
        t, off = _sample_time(body)
        M.append(CanonMeasurement("weight", float(body["weightGrams"]) / 1000.0, "kg", t, None, off, pid,
                                  orig_value=body["weightGrams"], orig_unit="g", provenance=prov, quality_flags=8))
    elif dtype == "body-fat":
        t, off = _sample_time(body)
        M.append(CanonMeasurement("body_fat", float(body["percentage"]), "%", t, None, off, pid, provenance=prov))
    elif dtype == "vo2-max":
        t, off = _sample_time(body)
        val = body.get("vo2Max") or body.get("milliliterPerMinuteKilogram")
        if val is None:
            out.error = "unrecognized vo2-max payload"
        else:
            M.append(CanonMeasurement("vo2max", float(val), "mL/kg/min", t, None, off, pid,
                                      provenance=Provenance.IMPORTED))
    elif dtype == "sleep":
        iv = body["interval"]
        stages = [CanonStage(_STAGE.get(st.get("type", ""), "unknown"), parse_instant(st["startTime"]),
                             parse_instant(st["endTime"]), st.get("type")) for st in body.get("stages") or []]
        out.sleeps.append(CanonSleep(parse_instant(iv["startTime"]), parse_instant(iv["endTime"]),
                                     parse_offset_minutes(iv.get("startUtcOffset")), pid, stages,
                                     provenance=prov, meta={"summary": body.get("summary"),
                                                            "type": body.get("type")},
                                     end_offset_min=parse_offset_minutes(iv.get("endUtcOffset"))))
    elif dtype == "exercise":
        s, e, off = _interval(body)
        ms = body.get("metricsSummary") or {}
        out.exercises.append(CanonExercise(
            str(body.get("exerciseType", "other")).lower(), s, e, off, pid, body.get("displayName"),
            distance_m=_num(ms.get("distanceMillimeters")) / 1000.0 if ms.get("distanceMillimeters") else None,
            active_kcal=_num(ms.get("caloriesKcal")), avg_hr=_num(ms.get("averageHeartRateBeatsPerMinute")),
            provenance=prov, meta={"heartRateZoneDurations": ms.get("heartRateZoneDurations")}))
    return out


def list_filter(dtype: str, start: datetime, end: datetime) -> str:
    """Build the AIP-160 filter documented for dataPoints.list."""
    s = start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    e = end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    field = SUPPORTED_TYPES[dtype]
    snake = "".join("_" + c.lower() if c.isupper() else c for c in field)
    if dtype == "sleep":
        return f'sleep.interval.end_time >= "{s}" AND sleep.interval.end_time < "{e}"'
    if dtype.startswith("daily-"):
        return f'{field}.date >= "{start.date().isoformat()}" AND {field}.date < "{(end.date() + timedelta(days=1)).isoformat()}"'
    if dtype in ("heart-rate", "heart-rate-variability", "oxygen-saturation", "weight", "body-fat", "vo2-max",
                 "respiratory-rate-sleep-summary"):
        return f'{snake}.sample_time.physical_time >= "{s}" AND {snake}.sample_time.physical_time < "{e}"'
    if dtype == "exercise":
        return f'exercise.interval.civil_start_time >= "{start.date().isoformat()}" AND exercise.interval.civil_start_time < "{(end.date() + timedelta(days=1)).isoformat()}"'
    return f'{snake}.interval.start_time >= "{s}" AND {snake}.interval.start_time < "{e}"'
