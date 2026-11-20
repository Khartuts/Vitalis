"""Health Connect adapter for records serialized by the VITALIS companion (schema vitalis.sync.v1).

The companion maps HC constants (stage types, exercise types, device types) to strings using the
compile-time-checked Jetpack constants, and converts unit objects with the Jetpack unit API
(e.g. Energy.inKilocalories), so values arrive in canonical units.
"""

from __future__ import annotations

from ..model.canonical import (CanonExercise, CanonMeasurement, CanonSleep, CanonStage, NormalizedRecord,
                               SourceInfo)
from ..model.metrics import Provenance
from ..timeutil import parse_instant, parse_offset_minutes

PROVIDER = "health_connect"

_SIMPLE_INSTANT = {
    # type: (field, metric, unit)
    "RestingHeartRateRecord": ("bpm", "resting_heart_rate", "bpm"),
    "HeartRateVariabilityRmssdRecord": ("rmssdMs", "hrv_rmssd", "ms"),
    "OxygenSaturationRecord": ("percent", "spo2", "%"),
    "RespiratoryRateRecord": ("rate", "respiratory_rate", "br/min"),
    "BodyTemperatureRecord": ("celsius", "body_temperature", "°C"),
    "WeightRecord": ("kg", "weight", "kg"),
    "BodyFatRecord": ("percent", "body_fat", "%"),
    "LeanBodyMassRecord": ("kg", "lean_mass", "kg"),
    "BodyWaterMassRecord": ("kg", "body_water_mass", "kg"),
    "BoneMassRecord": ("kg", "bone_mass", "kg"),
    "BasalMetabolicRateRecord": ("kcalPerDay", "bmr", "kcal/day"),
    "HeightRecord": ("meters", "height", "m"),
    "BloodGlucoseRecord": ("mmolPerL", "blood_glucose", "mmol/L"),
    "Vo2MaxRecord": ("vo2", "vo2max", "mL/kg/min"),
}

_SIMPLE_INTERVAL = {
    "StepsRecord": ("count", "steps", "count"),
    "DistanceRecord": ("meters", "distance", "m"),
    "ActiveCaloriesBurnedRecord": ("kcal", "active_energy", "kcal"),
    "TotalCaloriesBurnedRecord": ("kcal", "total_energy", "kcal"),
    "FloorsClimbedRecord": ("floors", "floors", "count"),
    "HydrationRecord": ("liters", "hydration", "L"),
    "NutritionRecord": ("kcal", "nutrition_energy", "kcal"),
}

_STAGE_MAP = {"awake": "awake", "sleeping": "asleep", "out_of_bed": "out_of_bed", "light": "light",
              "deep": "deep", "rem": "rem", "awake_in_bed": "awake_in_bed", "unknown": "unknown"}


def _source(rec: dict) -> SourceInfo:
    dev = rec.get("device") or {}
    return SourceInfo(PROVIDER, rec.get("origin"), dev.get("type"), dev.get("manufacturer"), dev.get("model"))


def _meta(rec: dict) -> dict:
    m = {}
    for k in ("recordingMethod", "lastModified", "clientRecordId"):
        if rec.get(k) is not None:
            m[k] = rec[k]
    return m


def _provenance(rec: dict) -> str:
    return Provenance.USER_ENTERED if rec.get("recordingMethod") == "manual_entry" else Provenance.MEASURED


def normalize(rec: dict) -> NormalizedRecord:
    rtype = str(rec.get("type", "unknown"))
    rid = rec.get("id")
    out = NormalizedRecord(rtype, rid, _source(rec))
    meta, prov = _meta(rec), _provenance(rec)

    if rtype in _SIMPLE_INSTANT:
        field, metric, unit = _SIMPLE_INSTANT[rtype]
        if rec.get(field) is None:
            out.error = f"missing field {field}"
            return out
        t = parse_instant(rec["time"])
        extra = {k: rec[k] for k in ("location", "method", "specimenSource", "mealType", "relationToMeal")
                 if rec.get(k) is not None}
        out.measurements.append(CanonMeasurement(metric, float(rec[field]), unit, t, None,
                                                 parse_offset_minutes(rec.get("zoneOffset")), rid,
                                                 orig_value=rec[field], orig_unit=unit, provenance=prov,
                                                 meta={**meta, **extra}))
        return out

    if rtype in _SIMPLE_INTERVAL:
        field, metric, unit = _SIMPLE_INTERVAL[rtype]
        if rec.get(field) is None:
            out.error = f"missing field {field}"
            return out
        out.measurements.append(CanonMeasurement(
            metric, float(rec[field]), unit, parse_instant(rec["startTime"]), parse_instant(rec["endTime"]),
            parse_offset_minutes(rec.get("startZoneOffset")), rid, orig_value=rec[field], orig_unit=unit,
            provenance=prov, meta=meta))
        return out

    if rtype in ("StepsAggregateHourly", "StepsAggregateDaily", "ActiveCaloriesAggregateHourly",
                 "DistanceAggregateHourly"):
        # De-duplicated totals from HealthConnectClient.aggregate / aggregateGroupByDuration (COUNT_TOTAL etc.).
        metric = {"StepsAggregateHourly": "steps", "StepsAggregateDaily": "steps_daily",
                  "ActiveCaloriesAggregateHourly": "active_energy", "DistanceAggregateHourly": "distance"}[rtype]
        unit = {"steps": "count", "steps_daily": "count", "active_energy": "kcal", "distance": "m"}[metric]
        if rec.get("value") is None:
            out.error = "missing aggregate value"
            return out
        s, e = parse_instant(rec["startTime"]), parse_instant(rec["endTime"])
        out.provider_record_id = f"agg:{rtype}:{rec['startTime']}"
        out.measurements.append(CanonMeasurement(
            metric, float(rec["value"]), unit, s, e, parse_offset_minutes(rec.get("startZoneOffset")),
            out.provider_record_id, provenance=Provenance.MEASURED,
            meta={"aggregate": True, "dataOrigins": rec.get("dataOrigins")}))
        return out

    if rtype == "HeartRateRecord":
        off = parse_offset_minutes(rec.get("startZoneOffset"))
        for s in rec.get("samples") or []:
            t = parse_instant(s["time"])
            out.measurements.append(CanonMeasurement("heart_rate", float(s["bpm"]), "bpm", t, None, off, rid,
                                                     sub_key=s["time"], orig_value=s["bpm"], orig_unit="bpm",
                                                     provenance=prov, meta=meta))
        return out

    if rtype == "SkinTemperatureRecord":
        off = parse_offset_minutes(rec.get("startZoneOffset"))
        base = rec.get("baselineC")
        for d in rec.get("deltas") or []:
            t = parse_instant(d["time"])
            out.measurements.append(CanonMeasurement(
                "skin_temp_delta", float(d["deltaC"]), "°C Δ", t, None, off, rid, sub_key=d["time"],
                orig_value=d["deltaC"], orig_unit="°C Δ", provenance=prov,
                meta={**meta, "baselineC": base, "location": rec.get("location")}))
        return out

    if rtype == "BloodPressureRecord":
        t = parse_instant(rec["time"])
        off = parse_offset_minutes(rec.get("zoneOffset"))
        extra = {k: rec.get(k) for k in ("bodyPosition", "measurementLocation") if rec.get(k) is not None}
        out.measurements.append(CanonMeasurement("bp_systolic", float(rec["systolicMmHg"]), "mmHg", t, None, off,
                                                 rid, sub_key="sys", provenance=prov, meta={**meta, **extra}))
        out.measurements.append(CanonMeasurement("bp_diastolic", float(rec["diastolicMmHg"]), "mmHg", t, None, off,
                                                 rid, sub_key="dia", provenance=prov, meta={**meta, **extra}))
        return out

    if rtype == "SleepSessionRecord":
        stages = []
        for st in rec.get("stages") or []:
            code = str(st.get("stage", "unknown"))
            stages.append(CanonStage(_STAGE_MAP.get(code, "unknown"), parse_instant(st["startTime"]),
                                     parse_instant(st["endTime"]), code))
        out.sleeps.append(CanonSleep(parse_instant(rec["startTime"]), parse_instant(rec["endTime"]),
                                     parse_offset_minutes(rec.get("startZoneOffset")), rid, stages, rec.get("title"),
                                     provenance=prov, meta=meta,
                                     end_offset_min=parse_offset_minutes(rec.get("endZoneOffset"))))
        return out

    if rtype == "ExerciseSessionRecord":
        out.exercises.append(CanonExercise(
            str(rec.get("exerciseType", "other")), parse_instant(rec["startTime"]), parse_instant(rec["endTime"]),
            parse_offset_minutes(rec.get("startZoneOffset")), rid, rec.get("title"), provenance=prov, meta=meta))
        return out

    out.unsupported = True
    return out
