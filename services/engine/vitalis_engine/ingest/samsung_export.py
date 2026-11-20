"""Samsung Health personal-data export adapter (adapter version samsung_export.v1).

Samsung's CSV export schema varies between app versions, so tables are recognized by file-name prefix
and columns by *suffix* (e.g. any column ending in `.heart_rate`). Assumptions, shown in the preview:
  * time columns are UTC wall-clock strings ("YYYY-MM-DD HH:MM:SS.fff") with a separate `time_offset`
    column such as "UTC+0100";
  * sleep-stage codes 40001=awake, 40002=light, 40003=deep, 40004=REM.
Nothing is imported without the user reviewing the preview.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from ..model.canonical import (CanonExercise, CanonMeasurement, CanonSleep, CanonStage, NormalizedRecord,
                               SourceInfo)
from ..model.metrics import Provenance
from ..timeutil import parse_offset_minutes

PROVIDER = "samsung_export"
ADAPTER_VERSION = "samsung_export.v1"
STAGE_CODES = {"40001": "awake", "40002": "light", "40003": "deep", "40004": "rem"}


@dataclass(frozen=True)
class TableSpec:
    key: str
    file_prefix: str
    required: tuple[str, ...]
    description: str


TABLES = [
    TableSpec("heart_rate", "com.samsung.shealth.tracker.heart_rate", ("start_time", "heart_rate"), "Heart rate samples"),
    TableSpec("spo2", "com.samsung.shealth.tracker.oxygen_saturation", ("start_time", "spo2"), "Blood oxygen"),
    TableSpec("steps", "com.samsung.shealth.tracker.pedometer_step_count", ("start_time", "count"), "Step counts"),
    TableSpec("sleep", "com.samsung.shealth.sleep", ("start_time", "end_time"), "Sleep sessions"),
    TableSpec("sleep_stage", "com.samsung.health.sleep_stage", ("start_time", "end_time", "stage"), "Sleep stages"),
    TableSpec("exercise", "com.samsung.shealth.exercise", ("start_time", "end_time", "exercise_type"), "Exercise"),
    TableSpec("weight", "com.samsung.health.weight", ("start_time", "weight"), "Weight / body composition"),
    TableSpec("stress", "com.samsung.shealth.stress", ("start_time", "score"), "Vendor stress score"),
    TableSpec("blood_pressure", "com.samsung.shealth.blood_pressure", ("start_time", "systolic", "diastolic"),
              "Blood pressure"),
]


def _col(header: list[str], suffix: str) -> str | None:
    for h in header:
        if h == suffix or h.endswith("." + suffix):
            return h
    return None


def _iter_csv_sources(path: Path) -> Iterator[tuple[str, str]]:
    if path.is_dir():
        for p in sorted(path.rglob("*.csv")):
            yield p.name, p.read_text(encoding="utf-8-sig", errors="replace")
    elif zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                if info.filename.lower().endswith(".csv") and not info.is_dir():
                    if info.file_size > 512 * 1024 * 1024:
                        continue
                    yield Path(info.filename).name, zf.read(info).decode("utf-8-sig", errors="replace")
    elif path.suffix.lower() == ".csv":
        yield path.name, path.read_text(encoding="utf-8-sig", errors="replace")
    else:
        raise ValueError("expected a Samsung Health export ZIP, folder or CSV file")


def _parse_table(text: str) -> tuple[list[str], list[dict]]:
    lines = text.splitlines()
    if not lines:
        return [], []
    start = 1 if (len(lines) > 1 and "," in lines[0] and lines[0].split(",")[0].startswith("com.samsung")
                  and not _looks_like_header(lines[0])) else 0
    reader = csv.reader(io.StringIO("\n".join(lines[start:])))
    rows = list(reader)
    if not rows:
        return [], []
    header = [h.strip() for h in rows[0]]
    out = []
    for r in rows[1:]:
        if not any(c.strip() for c in r):
            continue
        out.append({header[i]: r[i] for i in range(min(len(header), len(r))) if header[i]})
    return header, out


def _looks_like_header(line: str) -> bool:
    return any(tok.endswith(("start_time", "create_time", "datauuid")) for tok in line.split(","))


def _match(fname: str, header: list[str]) -> TableSpec | None:
    for spec in TABLES:
        if fname.startswith(spec.file_prefix + ".") or fname == spec.file_prefix + ".csv":
            if all(_col(header, req) for req in spec.required):
                return spec
    return None


def preview(path: str) -> dict:
    p = Path(path)
    files, unmatched = [], []
    for fname, text in _iter_csv_sources(p):
        header, rows = _parse_table(text)
        spec = _match(fname, header)
        if spec is None:
            unmatched.append({"file": fname, "columns": header[:12], "rows": len(rows)})
            continue
        start_col = _col(header, "start_time")
        times = sorted(r[start_col] for r in rows if r.get(start_col))
        mapped = [s for s in ("start_time", "end_time", "time_offset", "heart_rate", "spo2", "count", "stage",
                              "exercise_type", "weight", "body_fat", "score", "systolic", "diastolic", "datauuid",
                              "sleep_id", "distance", "calorie", "mean_heart_rate", "max_heart_rate", "sleep_score",
                              "skeletal_muscle_mass") if _col(header, s)]
        stage_codes = {}
        if spec.key == "sleep_stage":
            sc = _col(header, "stage")
            for r in rows:
                stage_codes[r.get(sc, "")] = stage_codes.get(r.get(sc, ""), 0) + 1
        files.append({"file": fname, "table": spec.key, "description": spec.description, "rows": len(rows),
                      "first": times[0] if times else None, "last": times[-1] if times else None,
                      "mapped_columns": mapped, "unmapped_columns": [h for h in header if not any(
                          h == m or h.endswith("." + m) for m in mapped)][:30],
                      "stage_codes": stage_codes or None})
    return {"adapter_version": ADAPTER_VERSION, "path": str(p), "tables": files, "unmatched_files": unmatched,
            "assumptions": ["Time columns are UTC; time_offset gives the original local offset.",
                            "Sleep stage codes: 40001 awake, 40002 light, 40003 deep, 40004 REM.",
                            "Vendor stress scores are imported verbatim and labeled proprietary."]}


def iter_records(path: str) -> Iterator[dict]:
    """Yield raw records {"type": "SAMSUNG_EXPORT.<table>", "file": ..., "row": {...}}."""
    for fname, text in _iter_csv_sources(Path(path)):
        header, rows = _parse_table(text)
        spec = _match(fname, header)
        if spec is None:
            continue
        for r in rows:
            yield {"type": f"SAMSUNG_EXPORT.{spec.key}", "file": fname, "row": r}


def _t(row: dict, key: str) -> datetime | None:
    col = next((k for k in row if k == key or k.endswith("." + key)), None)
    if not col or not row.get(col):
        return None
    s = row[col].strip()
    if re.fullmatch(r"\d{12,}", s):
        return datetime.fromtimestamp(int(s) / 1000, tz=timezone.utc)
    s = s.replace("T", " ").rstrip("Z")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"unparseable time {row[col]!r}")


def _v(row: dict, key: str) -> str | None:
    col = next((k for k in row if k == key or k.endswith("." + key)), None)
    val = row.get(col) if col else None
    return val.strip() if val and val.strip() else None


def _f(row: dict, key: str) -> float | None:
    v = _v(row, key)
    return float(v) if v is not None else None


def normalize(rec: dict) -> NormalizedRecord:
    table = rec["type"].split(".", 1)[1]
    row = rec["row"]
    uid = _v(row, "datauuid")
    out = NormalizedRecord(rec["type"], uid, SourceInfo(PROVIDER, "com.sec.android.app.shealth",
                                                         _v(row, "deviceuuid") and "device", None, None))
    off = parse_offset_minutes(_v(row, "time_offset"))
    start, end = _t(row, "start_time"), _t(row, "end_time")
    meta = {"adapter": ADAPTER_VERSION, "file": rec.get("file"), "time_basis": "utc_assumed"}
    if start is None:
        out.error = "missing start_time"
        return out
    M = out.measurements
    if table == "heart_rate":
        M.append(CanonMeasurement("heart_rate", _f(row, "heart_rate"), "bpm", start, None, off, uid, meta=meta))
    elif table == "spo2":
        M.append(CanonMeasurement("spo2", _f(row, "spo2"), "%", start, None, off, uid, meta=meta))
    elif table == "steps":
        M.append(CanonMeasurement("steps", _f(row, "count"), "count", start, end, off, uid, meta=meta))
    elif table == "stress":
        M.append(CanonMeasurement("stress_score_vendor", _f(row, "score"), "score", start, None, off, uid,
                                  provenance=Provenance.IMPORTED, meta={**meta, "proprietary": True}))
    elif table == "weight":
        M.append(CanonMeasurement("weight", _f(row, "weight"), "kg", start, None, off, uid, sub_key="w", meta=meta))
        if _f(row, "body_fat") is not None:
            M.append(CanonMeasurement("body_fat", _f(row, "body_fat"), "%", start, None, off, uid, sub_key="bf",
                                      meta=meta))
        if _f(row, "skeletal_muscle_mass") is not None:
            M.append(CanonMeasurement("skeletal_muscle_mass", _f(row, "skeletal_muscle_mass"), "kg", start, None, off,
                                      uid, sub_key="smm", meta=meta))
    elif table == "blood_pressure":
        M.append(CanonMeasurement("bp_systolic", _f(row, "systolic"), "mmHg", start, None, off, uid, sub_key="s",
                                  meta=meta))
        M.append(CanonMeasurement("bp_diastolic", _f(row, "diastolic"), "mmHg", start, None, off, uid, sub_key="d",
                                  meta=meta))
    elif table == "sleep":
        if end is None:
            out.error = "sleep without end_time"
            return out
        out.sleeps.append(CanonSleep(start, end, off, uid, [], meta=meta))
        score = _f(row, "sleep_score")
        if score is not None:
            M.append(CanonMeasurement("sleep_score", score, "score", end, None, off, uid,
                                      provenance=Provenance.IMPORTED, meta=meta))
    elif table == "sleep_stage":
        # Stages are stored as tiny sessions keyed by sleep_id; features.py merges them into their parent night.
        code = _v(row, "stage") or ""
        sleep_id = _v(row, "sleep_id")
        out.sleeps.append(CanonSleep(start, end or start, off, f"stage:{uid}",
                                     [CanonStage(STAGE_CODES.get(code, "unknown"), start, end or start, code)],
                                     meta={**meta, "stage_only": True, "parent_sleep_id": sleep_id}))
    elif table == "exercise":
        if end is None:
            out.error = "exercise without end_time"
            return out
        out.exercises.append(CanonExercise(str(_v(row, "exercise_type") or "other"), start, end, off, uid,
                                           distance_m=_f(row, "distance"), active_kcal=_f(row, "calorie"),
                                           avg_hr=_f(row, "mean_heart_rate"), max_hr=_f(row, "max_heart_rate"),
                                           meta=meta))
    else:
        out.unsupported = True
    for m in M:
        if m.value is None:
            out.error = f"empty value for {m.metric}"
            out.measurements = []
            break
    return out
