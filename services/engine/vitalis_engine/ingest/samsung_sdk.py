"""Samsung Health Data SDK adapter (records serialized by the companion's optional Samsung module).

Only fields documented in public Samsung material are mapped; any other field is retained in the
raw record and reported as `unmapped` so it can be reviewed instead of being guessed.
Documented (2026-11): SleepType.DURATION / SESSIONS / SLEEP_SCORE, BloodOxygenType.OXYGEN_SATURATION,
StepsType.TOTAL (aggregate), HeartRateType MIN/MAX (aggregate). Field names for heart-rate samples,
skin temperature, energy score and sleep-stage objects are behind a login-gated API reference and are
treated as *unverified* (flagged LOW_CONFIDENCE in metadata until confirmed on a device).
"""

from __future__ import annotations

from ..model.canonical import (CanonExercise, CanonMeasurement, CanonSleep, CanonStage, NormalizedRecord,
                               SourceInfo)
from ..model.metrics import Provenance
from ..timeutil import parse_instant, parse_offset_minutes

PROVIDER = "samsung_sdk"

# SDK type -> {field: (metric, unit, provenance, verified)}
_FIELDS = {
    "HEART_RATE": {"HEART_RATE": ("heart_rate", "bpm", Provenance.MEASURED, False)},
    "BLOOD_OXYGEN": {"OXYGEN_SATURATION": ("spo2", "%", Provenance.MEASURED, True)},
    "SKIN_TEMPERATURE": {"SKIN_TEMPERATURE": ("skin_temperature", "°C", Provenance.MEASURED, False)},
    "BODY_TEMPERATURE": {"BODY_TEMPERATURE": ("body_temperature", "°C", Provenance.MEASURED, False)},
    "ENERGY_SCORE": {"ENERGY_SCORE": ("energy_score", "score", Provenance.IMPORTED, False)},
    "BLOOD_PRESSURE": {"SYSTOLIC": ("bp_systolic", "mmHg", Provenance.MEASURED, False),
                       "DIASTOLIC": ("bp_diastolic", "mmHg", Provenance.MEASURED, False)},
    "BODY_COMPOSITION": {"WEIGHT": ("weight", "kg", Provenance.MEASURED, False),
                         "BODY_FAT": ("body_fat", "%", Provenance.MEASURED, False),
                         "SKELETAL_MUSCLE_MASS": ("skeletal_muscle_mass", "kg", Provenance.MEASURED, False),
                         "BASAL_METABOLIC_RATE": ("bmr", "kcal/day", Provenance.MEASURED, False)},
    "BLOOD_GLUCOSE": {"GLUCOSE_LEVEL": ("blood_glucose", "mmol/L", Provenance.MEASURED, False)},
    "STEPS_AGGREGATE": {"TOTAL": ("steps", "count", Provenance.MEASURED, True)},
    "FLOORS_CLIMBED": {"FLOOR": ("floors", "count", Provenance.MEASURED, False)},
    "WATER_INTAKE": {"AMOUNT": ("hydration", "L", Provenance.USER_ENTERED, False)},
}


def _source(rec: dict) -> SourceInfo:
    dev = rec.get("device") or {}
    ds = rec.get("dataSource") or {}
    return SourceInfo(PROVIDER, ds.get("appId") or "com.sec.android.app.shealth", dev.get("deviceType"),
                      dev.get("manufacturer"), dev.get("model"))


def normalize(rec: dict) -> NormalizedRecord:
    full_type = str(rec.get("type", "unknown"))
    sdk_type = full_type.split(".", 1)[1] if full_type.startswith("SAMSUNG_SDK.") else full_type
    uid = rec.get("uid")
    out = NormalizedRecord(full_type, uid, _source(rec))
    off = parse_offset_minutes(rec.get("zoneOffset"))
    start = parse_instant(rec["startTime"]) if rec.get("startTime") else None
    end = parse_instant(rec["endTime"]) if rec.get("endTime") else None
    fields = dict(rec.get("fields") or {})

    if sdk_type == "SLEEP":
        if start is None or end is None:
            out.error = "sleep without start/end"
            return out
        sessions = rec.get("sessions") or []
        score = fields.pop("SLEEP_SCORE", None)
        fields.pop("DURATION_MS", None)
        if not sessions:
            sessions = [{"startTime": rec["startTime"], "endTime": rec["endTime"], "stages": []}]
        for i, s in enumerate(sessions):
            stages = [CanonStage(str(st.get("stage", "unknown")).lower(), parse_instant(st["startTime"]),
                                 parse_instant(st["endTime"]), str(st.get("stage"))) for st in s.get("stages") or []]
            stages = [CanonStage(st.stage if st.stage in ("awake", "light", "deep", "rem") else "unknown",
                                 st.start, st.end, st.orig_code) for st in stages]
            out.sleeps.append(CanonSleep(parse_instant(s["startTime"]), parse_instant(s["endTime"]), off,
                                         f"{uid}#{i}" if uid else None, stages,
                                         meta={"samsung_uid": uid, "session_index": i,
                                               "stage_fields_verified": False}))
        if score is not None:
            out.measurements.append(CanonMeasurement("sleep_score", float(score), "score", end, None, off, uid,
                                                     provenance=Provenance.IMPORTED,
                                                     meta={"field": "DataType.SleepType.SLEEP_SCORE"}))
        out.unmapped_fields = sorted(fields.keys())
        return out

    if sdk_type == "EXERCISE":
        if start is None or end is None:
            out.error = "exercise without start/end"
            return out
        out.exercises.append(CanonExercise(str(fields.pop("EXERCISE_TYPE", "other")).lower(), start, end, off, uid,
                                           meta={"fields_verified": False}))
        out.unmapped_fields = sorted(fields.keys())
        return out

    mapping = _FIELDS.get(sdk_type)
    if mapping is None or start is None:
        out.unsupported = True
        return out
    for fname, (metric, unit, prov, verified) in mapping.items():
        if fields.get(fname) is None:
            continue
        val = float(fields.pop(fname))
        if metric == "blood_glucose" and rec.get("glucoseUnit") == "mg/dL":
            val = val / 18.0156
        out.measurements.append(CanonMeasurement(metric, val, unit, start, end, off, uid, sub_key=fname,
                                                 orig_value=val, orig_unit=unit, provenance=prov,
                                                 meta={"sdk_field": fname, "field_verified": verified}))
    out.unmapped_fields = sorted(fields.keys())
    if not out.measurements:
        out.error = "no mappable fields"
    return out
