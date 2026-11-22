"""Canonical metric catalog.

Every stored measurement uses one of these metric keys with its canonical unit.
`valid_range` is a physiological plausibility range used for quality flagging
(values outside are stored but flagged, never silently dropped).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntFlag


class Provenance:
    MEASURED = "measured"          # sensor measurement reported by a device
    IMPORTED = "imported"          # value computed by a third party (e.g. Samsung sleep score)
    CALCULATED = "calculated"      # deterministic VITALIS computation from stored data
    ESTIMATED = "estimated"        # VITALIS model output with stated uncertainty
    USER_ENTERED = "user_entered"  # journal / manual entry
    AI_INTERPRETED = "ai_interpreted"
    SYNTHETIC = "synthetic"        # demo data – never mixed into real profiles

    ALL = (MEASURED, IMPORTED, CALCULATED, ESTIMATED, USER_ENTERED, AI_INTERPRETED, SYNTHETIC)


class QualityFlag(IntFlag):
    NONE = 0
    OUT_OF_RANGE = 1
    OUTLIER = 2
    DUPLICATE = 4
    UNIT_CONVERTED = 8
    TZ_INFERRED = 16
    LOW_CONFIDENCE = 32
    FUTURE_TIMESTAMP = 64
    CONFLICT = 128


@dataclass(frozen=True)
class MetricDef:
    key: str
    unit: str
    kind: str  # sample | interval | daily | score | event
    label: str
    valid_range: tuple[float, float] | None
    aggregation: str  # mean | sum | last | min | max
    description: str
    higher_is: str = "neutral"  # better | worse | neutral (for display only)


METRICS: dict[str, MetricDef] = {m.key: m for m in [
    MetricDef("heart_rate", "bpm", "sample", "Heart rate", (25, 240), "mean", "Instantaneous heart rate sample."),
    MetricDef("resting_heart_rate", "bpm", "daily", "Resting heart rate (reported)", (25, 130), "mean",
              "Resting heart rate as reported by the source app.", "worse"),
    MetricDef("hrv_rmssd", "ms", "sample", "HRV (RMSSD)", (1, 300), "mean",
              "Heart-rate variability as RMSSD reported by a source; methodology is source-specific.", "better"),
    MetricDef("hrv_sdnn", "ms", "sample", "HRV (SDNN)", (1, 400), "mean",
              "Heart-rate variability as SDNN reported by a source.", "better"),
    MetricDef("hrv_rmssd_daily", "ms", "daily", "Nightly HRV (RMSSD, daily summary)", (1, 300), "mean",
              "Daily RMSSD summary computed by the source (e.g. Google Health daily HRV).", "better"),
    MetricDef("spo2", "%", "sample", "Blood oxygen (SpO2)", (50, 100), "mean", "Oxygen saturation sample."),
    MetricDef("spo2_daily_avg", "%", "daily", "Nightly SpO2 average (source)", (50, 100), "mean",
              "Source-computed nightly SpO2 average."),
    MetricDef("respiratory_rate", "br/min", "sample", "Respiratory rate", (4, 60), "mean", "Breaths per minute."),
    MetricDef("skin_temp_delta", "°C Δ", "sample", "Skin temperature deviation", (-5, 5), "mean",
              "Skin temperature relative to the source's baseline (not an absolute temperature)."),
    MetricDef("skin_temperature", "°C", "sample", "Skin temperature", (20, 42), "mean", "Absolute skin temperature."),
    MetricDef("body_temperature", "°C", "sample", "Body temperature", (33, 43), "mean", "Body temperature."),
    MetricDef("room_temperature", "°C", "sample", "Room temperature", (0, 45), "mean",
              "Bedroom air temperature from a room sensor (e.g. SmartThings)."),
    MetricDef("steps", "count", "interval", "Steps", (0, 100000), "sum", "Step count over an interval.", "better"),
    MetricDef("steps_daily", "count", "daily", "Steps (daily total, source-aggregated)", (0, 150000), "sum",
              "Daily step total computed by the source's de-duplicating aggregate API.", "better"),
    MetricDef("distance", "m", "interval", "Distance", (0, 300000), "sum", "Distance over an interval."),
    MetricDef("active_energy", "kcal", "interval", "Active energy", (0, 10000), "sum", "Active calories."),
    MetricDef("total_energy", "kcal", "interval", "Total energy", (0, 15000), "sum", "Total calories."),
    MetricDef("floors", "count", "interval", "Floors climbed", (0, 1000), "sum", "Floors climbed."),
    MetricDef("weight", "kg", "sample", "Weight", (20, 350), "last", "Body weight."),
    MetricDef("body_fat", "%", "sample", "Body fat", (2, 70), "last", "Body fat percentage."),
    MetricDef("lean_mass", "kg", "sample", "Lean body mass", (10, 200), "last", "Lean body mass."),
    MetricDef("skeletal_muscle_mass", "kg", "sample", "Skeletal muscle mass", (5, 120), "last", "Skeletal muscle mass."),
    MetricDef("body_water_mass", "kg", "sample", "Body water mass", (5, 150), "last", "Body water mass."),
    MetricDef("bone_mass", "kg", "sample", "Bone mass", (0.5, 10), "last", "Bone mass."),
    MetricDef("bmr", "kcal/day", "sample", "Basal metabolic rate", (500, 5000), "last", "Basal metabolic rate."),
    MetricDef("height", "m", "sample", "Height", (0.5, 2.6), "last", "Height."),
    MetricDef("bp_systolic", "mmHg", "sample", "Systolic blood pressure", (60, 260), "mean", "Systolic pressure."),
    MetricDef("bp_diastolic", "mmHg", "sample", "Diastolic blood pressure", (30, 160), "mean", "Diastolic pressure."),
    MetricDef("blood_glucose", "mmol/L", "sample", "Blood glucose", (1, 35), "mean", "Blood glucose."),
    MetricDef("vo2max", "mL/kg/min", "sample", "VO2 max (source estimate)", (10, 95), "last",
              "Cardiorespiratory fitness estimate reported by a source."),
    MetricDef("hydration", "L", "interval", "Water intake", (0, 15), "sum", "Water intake."),
    MetricDef("nutrition_energy", "kcal", "interval", "Food energy", (0, 15000), "sum", "Logged food energy."),
    MetricDef("sleep_score", "score", "score", "Sleep score (Samsung, imported)", (0, 100), "last",
              "Proprietary Samsung Health sleep score, imported verbatim."),
    MetricDef("energy_score", "score", "score", "Energy Score (Samsung, imported)", (0, 100), "last",
              "Proprietary Samsung Health Energy Score, imported verbatim."),
    MetricDef("stress_score_vendor", "score", "score", "Stress (vendor score, imported)", (0, 100), "mean",
              "Manufacturer stress score from an export, imported verbatim (proprietary method)."),
    MetricDef("irregular_rhythm_event", "event", "event", "Irregular rhythm notification", None, "last",
              "Authorized notification record; displayed verbatim and never interpreted."),
    MetricDef("sleep_apnea_event", "event", "event", "Sleep apnea feature record", None, "last",
              "Authorized record; displayed verbatim and never interpreted."),
    MetricDef("ibi", "ms", "sample", "Inter-beat interval", (250, 2500), "mean",
              "Beat-to-beat interval (only from sources that genuinely supply IBIs)."),
]}


def metric(key: str) -> MetricDef:
    try:
        return METRICS[key]
    except KeyError as exc:  # pragma: no cover - programming error
        raise KeyError(f"Unknown metric '{key}'") from exc


def in_valid_range(key: str, value: float) -> bool:
    rng = METRICS[key].valid_range
    if rng is None:
        return True
    return rng[0] <= value <= rng[1]
