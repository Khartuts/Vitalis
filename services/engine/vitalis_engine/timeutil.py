"""Time handling: canonical UTC storage, original offsets, DST-correct local dates."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

DEFAULT_TZ = "Europe/Zagreb"

_OFFSET_RE = re.compile(r"^(?:UTC|GMT)?\s*([+-])(\d{1,2}):?(\d{2})?$", re.IGNORECASE)


def parse_instant(value: str | int | float | datetime) -> datetime:
    """Parse an RFC3339 timestamp / epoch milliseconds into an aware UTC datetime."""
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            raise ValueError("naive datetime is not an instant")
        return dt.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        # Heuristic: values above 1e11 are milliseconds.
        seconds = value / 1000.0 if abs(value) > 1e11 else float(value)
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    s = value.strip()
    if s.endswith("Z") or s.endswith("z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        raise ValueError(f"timestamp without offset: {value!r}")
    return dt.astimezone(timezone.utc)


def parse_offset_minutes(value: str | int | None) -> int | None:
    """Parse offsets like '+01:00', 'UTC+0100', '+02', '3600s', 3600 (seconds) into minutes."""
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return value // 60
    s = str(value).strip()
    if s.endswith("s") and re.match(r"^-?\d+(\.\d+)?s$", s):
        return int(round(float(s[:-1]) / 60.0))
    if s.upper() in ("Z", "UTC", "GMT"):
        return 0
    m = _OFFSET_RE.match(s)
    if not m:
        raise ValueError(f"unrecognized offset {value!r}")
    sign = -1 if m.group(1) == "-" else 1
    return sign * (int(m.group(2)) * 60 + int(m.group(3) or 0))


def to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def local_datetime(utc: datetime, offset_min: int | None, tz_name: str = DEFAULT_TZ) -> datetime:
    """Local wall-clock time: the original recorded offset if known, else the display timezone."""
    if offset_min is not None:
        return utc.astimezone(timezone(timedelta(minutes=offset_min)))
    return utc.astimezone(ZoneInfo(tz_name))


def local_date(utc: datetime, offset_min: int | None, tz_name: str = DEFAULT_TZ) -> date:
    return local_datetime(utc, offset_min, tz_name).date()


def day_bounds_utc(d: date, tz_name: str = DEFAULT_TZ) -> tuple[datetime, datetime]:
    """UTC bounds of a local calendar day (23 or 25 h on DST transitions)."""
    tz = ZoneInfo(tz_name)
    start = datetime(d.year, d.month, d.day, tzinfo=tz)
    nd = d + timedelta(days=1)
    end = datetime(nd.year, nd.month, nd.day, tzinfo=tz)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def minutes_after_local_midnight(dt_local: datetime, ref_day: date) -> float:
    """Minutes between local midnight of ref_day and dt_local (negative if before)."""
    midnight = datetime(ref_day.year, ref_day.month, ref_day.day, tzinfo=dt_local.tzinfo)
    return (dt_local - midnight).total_seconds() / 60.0


def daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def parse_date(s: str | date) -> date:
    if isinstance(s, date) and not isinstance(s, datetime):
        return s
    return date.fromisoformat(str(s)[:10])
