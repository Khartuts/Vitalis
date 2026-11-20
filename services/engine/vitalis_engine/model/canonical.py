"""Canonical (source-independent) record types produced by adapters."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .metrics import Provenance

RECORD_NS = uuid.UUID("6a8f3c1e-9b2d-4e7a-8c55-1f0d2b9e7a10")

SLEEP_STAGES = ("awake", "light", "deep", "rem", "asleep", "out_of_bed", "awake_in_bed", "unknown")
ASLEEP_STAGES = ("light", "deep", "rem", "asleep")


@dataclass(frozen=True)
class SourceInfo:
    provider: str
    app_package: str | None = None
    device_type: str | None = None
    device_manufacturer: str | None = None
    device_model: str | None = None


@dataclass
class CanonMeasurement:
    metric: str
    value: float | None
    unit: str
    start: datetime
    end: datetime | None = None
    offset_min: int | None = None
    provider_record_id: str | None = None
    sub_key: str = ""
    orig_value: Any = None
    orig_unit: str | None = None
    value_text: str | None = None
    provenance: str = Provenance.MEASURED
    confidence: float | None = None
    quality_flags: int = 0
    meta: dict = field(default_factory=dict)


@dataclass
class CanonStage:
    stage: str
    start: datetime
    end: datetime
    orig_code: str | None = None


@dataclass
class CanonSleep:
    start: datetime
    end: datetime
    offset_min: int | None = None
    provider_record_id: str | None = None
    stages: list[CanonStage] = field(default_factory=list)
    title: str | None = None
    provenance: str = Provenance.MEASURED
    quality_flags: int = 0
    meta: dict = field(default_factory=dict)
    end_offset_min: int | None = None


@dataclass
class CanonExercise:
    exercise_type: str
    start: datetime
    end: datetime
    offset_min: int | None = None
    provider_record_id: str | None = None
    title: str | None = None
    distance_m: float | None = None
    active_kcal: float | None = None
    avg_hr: float | None = None
    max_hr: float | None = None
    provenance: str = Provenance.MEASURED
    quality_flags: int = 0
    meta: dict = field(default_factory=dict)


@dataclass
class NormalizedRecord:
    """Result of normalizing ONE raw provider record."""
    record_type: str
    provider_record_id: str | None
    source: SourceInfo
    measurements: list[CanonMeasurement] = field(default_factory=list)
    sleeps: list[CanonSleep] = field(default_factory=list)
    exercises: list[CanonExercise] = field(default_factory=list)
    unsupported: bool = False
    unmapped_fields: list[str] = field(default_factory=list)
    error: str | None = None


def stable_record_id(provider: str, provider_record_id: str | None, kind: str, start_iso: str, sub_key: str = "",
                     fallback: str = "") -> str:
    basis = f"{provider}|{provider_record_id or 'h:' + fallback}|{kind}|{start_iso}|{sub_key}"
    return str(uuid.uuid5(RECORD_NS, basis))
