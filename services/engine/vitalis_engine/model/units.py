"""Unit normalization. Only physically compatible conversions are allowed."""

from __future__ import annotations

_CONVERSIONS: dict[tuple[str, str], float | None] = {
    # (from, to): factor  (value_to = value_from * factor)
    ("bpm", "bpm"): 1.0, ("1/min", "bpm"): 1.0, ("beats/min", "bpm"): 1.0,
    ("ms", "ms"): 1.0, ("s", "ms"): 1000.0, ("milliseconds", "ms"): 1.0,
    ("%", "%"): 1.0, ("percent", "%"): 1.0, ("fraction", "%"): 100.0,
    ("kg", "kg"): 1.0, ("g", "kg"): 0.001, ("grams", "kg"): 0.001, ("lb", "kg"): 0.45359237, ("pounds", "kg"): 0.45359237,
    ("m", "m"): 1.0, ("km", "m"): 1000.0, ("mm", "m"): 0.001, ("millimeters", "m"): 0.001, ("cm", "m"): 0.01,
    ("mi", "m"): 1609.344, ("meters", "m"): 1.0,
    ("kcal", "kcal"): 1.0, ("cal", "kcal"): 0.001, ("kj", "kcal"): 1 / 4.184, ("j", "kcal"): 1 / 4184.0,
    ("°c", "°C"): 1.0, ("c", "°C"): 1.0, ("celsius", "°C"): 1.0,
    ("°c δ", "°C Δ"): 1.0, ("°cδ", "°C Δ"): 1.0, ("delta_c", "°C Δ"): 1.0,
    ("mmhg", "mmHg"): 1.0, ("kpa", "mmHg"): 7.50061683,
    ("mmol/l", "mmol/L"): 1.0, ("mg/dl", "mmol/L"): 1 / 18.0156,
    ("l", "L"): 1.0, ("ml", "L"): 0.001, ("liters", "L"): 1.0,
    ("count", "count"): 1.0, ("steps", "count"): 1.0, ("floors", "count"): 1.0,
    ("br/min", "br/min"): 1.0, ("breaths/min", "br/min"): 1.0, ("rpm", "br/min"): 1.0,
    ("ml/kg/min", "mL/kg/min"): 1.0,
    ("score", "score"): 1.0,
    ("kcal/day", "kcal/day"): 1.0, ("w", "kcal/day"): 20.6362,  # 1 W = 86400 J/day = 20.636 kcal/day
    ("event", "event"): 1.0,
}


class UnitError(ValueError):
    pass


def convert(value: float, from_unit: str, to_unit: str) -> float:
    """Convert value between compatible units. Fahrenheit is handled explicitly."""
    f = from_unit.strip().lower()
    if f in ("°f", "f", "fahrenheit"):
        if to_unit == "°C":
            return (value - 32.0) * 5.0 / 9.0
        if to_unit == "°C Δ":
            return value * 5.0 / 9.0
        raise UnitError(f"Cannot convert {from_unit} to {to_unit}")
    key = (f, to_unit)
    if key not in _CONVERSIONS:
        # exact match fallback
        if from_unit == to_unit:
            return value
        raise UnitError(f"Incompatible units: {from_unit} -> {to_unit}")
    factor = _CONVERSIONS[key]
    assert factor is not None
    return value * factor


def is_converted(from_unit: str, to_unit: str) -> bool:
    return from_unit.strip().lower() not in (to_unit.lower(),)
