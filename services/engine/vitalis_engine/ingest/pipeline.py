"""Import pipeline: raw retention -> normalization -> quality flags -> idempotent upsert -> dedupe."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable

from ..db.connection import Database, utcnow
from ..model.canonical import (CanonExercise, CanonMeasurement, CanonSleep, NormalizedRecord, SourceInfo,
                               stable_record_id)
from ..model.metrics import METRICS, QualityFlag, in_valid_range
from ..timeutil import DEFAULT_TZ, local_date, to_iso

Adapter = Callable[[dict], NormalizedRecord]

DEFAULT_SOURCE_PRIORITY = ["samsung_sdk", "health_connect", "google_health", "samsung_export", "manual",
                           "vitalis", "synthetic"]


@dataclass
class ImportSummary:
    batch_id: str
    provider: str
    import_method: str
    received: int = 0
    inserted: int = 0
    updated: int = 0
    duplicate: int = 0
    rejected: int = 0
    deleted: int = 0
    unsupported_types: Counter = field(default_factory=Counter)
    unmapped_fields: Counter = field(default_factory=Counter)
    errors: list[str] = field(default_factory=list)
    min_date: str | None = None
    max_date: str | None = None
    already_imported: bool = False

    def as_dict(self) -> dict:
        return {
            "batch_id": self.batch_id, "provider": self.provider, "import_method": self.import_method,
            "received": self.received, "inserted": self.inserted, "updated": self.updated,
            "duplicate": self.duplicate, "rejected": self.rejected, "deleted": self.deleted,
            "unsupported_types": dict(self.unsupported_types), "unmapped_fields": dict(self.unmapped_fields),
            "errors": self.errors[:50], "min_date": self.min_date, "max_date": self.max_date,
            "already_imported": self.already_imported,
        }


def _sha(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _upsert_source(conn, src: SourceInfo, now: str) -> int:
    key = (src.provider, src.app_package, src.device_type, src.device_manufacturer, src.device_model)
    row = conn.execute(
        "SELECT id FROM sources WHERE provider IS ? AND app_package IS ? AND device_type IS ? "
        "AND device_manufacturer IS ? AND device_model IS ?", key).fetchone()
    if row:
        conn.execute("UPDATE sources SET last_seen=? WHERE id=?", (now, row[0]))
        return int(row[0])
    cur = conn.execute(
        "INSERT INTO sources(provider, app_package, device_type, device_manufacturer, device_model, first_seen, last_seen)"
        " VALUES (?,?,?,?,?,?,?)", key + (now, now))
    return int(cur.lastrowid)


class Importer:
    def __init__(self, db: Database, tz_name: str | None = None):
        self.db = db
        self.tz = tz_name or db.get_pref("display_timezone", DEFAULT_TZ)

    def batch_exists(self, batch_id: str) -> bool:
        row = self.db.query_one("SELECT status FROM import_batches WHERE id=?", (batch_id,))
        return bool(row and row["status"] in ("completed", "partial"))

    def run(self, *, provider: str, import_method: str, records: Iterable[dict], adapter: Adapter,
            batch_id: str | None = None, deletions: Iterable[dict] = (), detail: dict | None = None,
            progress: Callable[[int], None] | None = None) -> ImportSummary:
        batch_id = batch_id or str(uuid.uuid4())
        summary = ImportSummary(batch_id=batch_id, provider=provider, import_method=import_method)
        if self.batch_exists(batch_id):
            summary.already_imported = True
            return summary
        now = utcnow()
        future_limit = datetime.now(timezone.utc) + timedelta(days=1)
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO import_batches(id, provider, import_method, started_at, status, detail)"
                " VALUES (?,?,?,?,?,?)", (batch_id, provider, import_method, now, "running", json.dumps(detail or {})))
            source_cache: dict[SourceInfo, int] = {}
            for i, raw in enumerate(records):
                summary.received += 1
                if progress and i % 500 == 0:
                    progress(i)
                try:
                    norm = adapter(raw)
                except Exception as exc:  # adapter bug or malformed record: keep raw, report
                    summary.rejected += 1
                    summary.errors.append(f"record {i}: {type(exc).__name__}: {exc}")
                    norm = None
                payload_hash = _sha(raw)
                prid = norm.provider_record_id if norm else None
                rtype = norm.record_type if norm else str(raw.get("type") or raw.get("dataType") or "unknown")
                existing_raw = conn.execute(
                    "SELECT id FROM raw_records WHERE provider=? AND payload_sha256=? LIMIT 1",
                    (provider, payload_hash)).fetchone()
                if existing_raw:
                    raw_id = int(existing_raw[0])
                else:
                    raw_id = int(conn.execute(
                        "INSERT INTO raw_records(batch_id, provider, record_type, provider_record_id, payload,"
                        " payload_sha256, received_at) VALUES (?,?,?,?,?,?,?)",
                        (batch_id, provider, rtype, prid, json.dumps(raw, separators=(",", ":")), payload_hash, now)
                    ).lastrowid)
                if norm is None:
                    continue
                if norm.unsupported:
                    summary.unsupported_types[norm.record_type] += 1
                    continue
                if norm.error:
                    summary.rejected += 1
                    summary.errors.append(f"{norm.record_type}: {norm.error}")
                    continue
                for f in norm.unmapped_fields:
                    summary.unmapped_fields[f"{norm.record_type}.{f}"] += 1
                if norm.source not in source_cache:
                    source_cache[norm.source] = _upsert_source(conn, norm.source, now)
                sid = source_cache[norm.source]
                for m in norm.measurements:
                    self._store_measurement(conn, m, provider, import_method, batch_id, raw_id, sid, now,
                                            future_limit, summary)
                for s in norm.sleeps:
                    self._store_sleep(conn, s, provider, import_method, batch_id, raw_id, sid, now, summary)
                for e in norm.exercises:
                    self._store_exercise(conn, e, provider, import_method, batch_id, raw_id, sid, now, summary)
            for d in deletions:
                summary.deleted += self._apply_deletion(conn, provider, str(d.get("id")), now)
            conn.execute(
                "UPDATE import_batches SET finished_at=?, status=?, records_received=?, records_inserted=?,"
                " records_updated=?, records_duplicate=?, records_rejected=?, records_deleted=?, detail=? WHERE id=?",
                (utcnow(), "completed" if not summary.errors else "partial", summary.received, summary.inserted,
                 summary.updated, summary.duplicate, summary.rejected, summary.deleted,
                 json.dumps({**(detail or {}), **summary.as_dict()}), batch_id))
        return summary

    # ------------------------------------------------------------------------------
    def _track_dates(self, summary: ImportSummary, d: str) -> None:
        if summary.min_date is None or d < summary.min_date:
            summary.min_date = d
        if summary.max_date is None or d > summary.max_date:
            summary.max_date = d

    def _store_measurement(self, conn, m: CanonMeasurement, provider, method, batch_id, raw_id, sid, now,
                           future_limit, summary):
        flags = m.quality_flags
        if m.metric not in METRICS:
            summary.rejected += 1
            summary.errors.append(f"unknown metric {m.metric}")
            return
        if m.value is not None and not in_valid_range(m.metric, m.value):
            flags |= QualityFlag.OUT_OF_RANGE
        if m.start > future_limit:
            flags |= QualityFlag.FUTURE_TIMESTAMP
        if m.offset_min is None:
            flags |= QualityFlag.TZ_INFERRED
        start_iso = to_iso(m.start)
        rid = stable_record_id(provider, m.provider_record_id, m.metric, start_iso, m.sub_key,
                               fallback=f"{m.value}|{to_iso(m.end) if m.end else ''}")
        ld = local_date(m.start if m.end is None else m.start, m.offset_min, self.tz).isoformat()
        self._track_dates(summary, ld)
        row = conn.execute("SELECT value_num, value_text, end_utc, deleted_at FROM measurements WHERE record_id=?",
                           (rid,)).fetchone()
        end_iso = to_iso(m.end) if m.end else None
        meta = json.dumps(m.meta) if m.meta else None
        if row:
            if row[0] == m.value and row[1] == m.value_text and row[2] == end_iso and row[3] is None:
                summary.duplicate += 1
                return
            conn.execute(
                "UPDATE measurements SET value_num=?, value_text=?, end_utc=?, quality_flags=?, updated_at=?,"
                " raw_id=?, deleted_at=NULL, meta=? WHERE record_id=?",
                (m.value, m.value_text, end_iso, int(flags), now, raw_id, meta, rid))
            summary.updated += 1
            return
        conn.execute(
            "INSERT INTO measurements(record_id, provider, provider_record_id, source_id, metric, value_num, value_text,"
            " unit, orig_value, orig_unit, start_utc, end_utc, utc_offset_min, local_date, provenance, quality_flags,"
            " confidence, import_method, batch_id, raw_id, imported_at, updated_at, meta)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rid, provider, m.provider_record_id, sid, m.metric, m.value, m.value_text, m.unit,
             None if m.orig_value is None else str(m.orig_value), m.orig_unit, start_iso, end_iso, m.offset_min,
             ld, m.provenance, int(flags), m.confidence, method, batch_id, raw_id, now, now, meta))
        summary.inserted += 1

    def _store_sleep(self, conn, s: CanonSleep, provider, method, batch_id, raw_id, sid, now, summary):
        if s.end <= s.start:
            summary.rejected += 1
            summary.errors.append("sleep session with non-positive duration")
            return
        start_iso, end_iso = to_iso(s.start), to_iso(s.end)
        rid = stable_record_id(provider, s.provider_record_id, "sleep", start_iso, fallback=end_iso)
        ld = local_date(s.end, _end_offset(s, self.tz), self.tz).isoformat()
        self._track_dates(summary, ld)
        flags = s.quality_flags | (QualityFlag.TZ_INFERRED if s.offset_min is None else 0)
        row = conn.execute("SELECT end_utc, deleted_at, meta FROM sleep_sessions WHERE record_id=?", (rid,)).fetchone()
        n_stages = conn.execute("SELECT count(*) FROM sleep_stages WHERE session_record_id=?", (rid,)).fetchone()[0] \
            if row else 0
        meta = json.dumps(s.meta) if s.meta else None
        if row and row[0] == end_iso and row[1] is None and n_stages == len(s.stages) and row[2] == meta:
            summary.duplicate += 1
            return
        if row:
            conn.execute("UPDATE sleep_sessions SET end_utc=?, local_date=?, has_stages=?, updated_at=?, raw_id=?,"
                         " deleted_at=NULL, meta=?, quality_flags=?, end_offset_min=? WHERE record_id=?",
                         (end_iso, ld, 1 if s.stages else 0, now, raw_id, meta, int(flags), s.end_offset_min, rid))
            conn.execute("DELETE FROM sleep_stages WHERE session_record_id=?", (rid,))
            summary.updated += 1
        else:
            conn.execute(
                "INSERT INTO sleep_sessions(record_id, provider, provider_record_id, source_id, start_utc, end_utc,"
                " utc_offset_min, local_date, title, has_stages, provenance, quality_flags, import_method, batch_id,"
                " raw_id, imported_at, updated_at, meta, end_offset_min) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (rid, provider, s.provider_record_id, sid, start_iso, end_iso, s.offset_min, ld, s.title,
                 1 if s.stages else 0, s.provenance, int(flags), method, batch_id, raw_id, now, now, meta,
                 s.end_offset_min))
            summary.inserted += 1
        conn.executemany(
            "INSERT INTO sleep_stages(session_record_id, stage, start_utc, end_utc, orig_code) VALUES (?,?,?,?,?)",
            [(rid, st.stage, to_iso(st.start), to_iso(st.end), st.orig_code) for st in s.stages if st.end > st.start])

    def _store_exercise(self, conn, e: CanonExercise, provider, method, batch_id, raw_id, sid, now, summary):
        if e.end <= e.start:
            summary.rejected += 1
            return
        start_iso, end_iso = to_iso(e.start), to_iso(e.end)
        rid = stable_record_id(provider, e.provider_record_id, "exercise", start_iso, fallback=end_iso)
        ld = local_date(e.start, e.offset_min, self.tz).isoformat()
        self._track_dates(summary, ld)
        row = conn.execute("SELECT end_utc, deleted_at FROM exercise_sessions WHERE record_id=?", (rid,)).fetchone()
        vals = (e.exercise_type, e.title, start_iso, end_iso, e.offset_min, ld, e.distance_m, e.active_kcal, e.avg_hr,
                e.max_hr, e.provenance, int(e.quality_flags), json.dumps(e.meta) if e.meta else None)
        if row and row[0] == end_iso and row[1] is None:
            summary.duplicate += 1
            return
        if row:
            conn.execute("UPDATE exercise_sessions SET exercise_type=?, title=?, start_utc=?, end_utc=?,"
                         " utc_offset_min=?, local_date=?, distance_m=?, active_kcal=?, avg_hr=?, max_hr=?,"
                         " provenance=?, quality_flags=?, meta=?, updated_at=?, deleted_at=NULL WHERE record_id=?",
                         vals + (now, rid))
            summary.updated += 1
            return
        conn.execute(
            "INSERT INTO exercise_sessions(record_id, provider, provider_record_id, source_id, exercise_type, title,"
            " start_utc, end_utc, utc_offset_min, local_date, distance_m, active_kcal, avg_hr, max_hr, provenance,"
            " quality_flags, meta, import_method, batch_id, raw_id, imported_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rid, provider, e.provider_record_id, sid) + vals + (method, batch_id, raw_id, now, now))
        summary.inserted += 1

    def _apply_deletion(self, conn, provider: str, provider_record_id: str, now: str) -> int:
        n = 0
        for table in ("measurements", "sleep_sessions", "exercise_sessions"):
            n += conn.execute(
                f"UPDATE {table} SET deleted_at=? WHERE provider=? AND provider_record_id=? AND deleted_at IS NULL",
                (now, provider, provider_record_id)).rowcount
        return n


def _end_offset(s: CanonSleep, tz: str) -> int | None:
    """End offset: explicit if supplied; otherwise DST-aware zone conversion when the start offset matches the
    display zone (offset None => zone conversion), else the start offset (travel)."""
    if s.end_offset_min is not None:
        return s.end_offset_min
    return zone_consistent_offset(s.start, s.offset_min, tz)


def zone_consistent_offset(start: datetime, offset_min: int | None, tz: str) -> int | None:
    from zoneinfo import ZoneInfo
    if offset_min is None:
        return None
    zoff = start.astimezone(ZoneInfo(tz)).utcoffset()
    return None if zoff is not None and int(zoff.total_seconds() // 60) == offset_min else offset_min


# ------------------------------------------------------------------------------------------
# Cross-source duplicate resolution
# ------------------------------------------------------------------------------------------

SAMPLE_TOLERANCE = {"heart_rate": 3.0, "spo2": 1.0, "respiratory_rate": 1.0, "hrv_rmssd": 3.0, "weight": 0.3,
                    "body_fat": 0.5, "bp_systolic": 2.0, "bp_diastolic": 2.0, "skin_temp_delta": 0.1,
                    "resting_heart_rate": 2.0, "blood_glucose": 0.2}


def source_priority(db: Database) -> list[str]:
    pr = db.get_pref("source_priority", DEFAULT_SOURCE_PRIORITY)
    return list(pr) + [p for p in DEFAULT_SOURCE_PRIORITY if p not in pr]


def resolve_duplicates(db: Database, start_date: str, end_date: str) -> dict:
    """Mark lower-priority cross-provider duplicates as superseded (reversible, nothing is deleted)."""
    prio = {p: i for i, p in enumerate(source_priority(db))}
    stats = Counter()
    with db.transaction() as conn:
        # reset previous decisions in range so a changed priority policy is applied consistently
        conn.execute("UPDATE measurements SET superseded_by=NULL WHERE local_date BETWEEN ? AND ?",
                     (start_date, end_date))
        conn.execute("UPDATE sleep_sessions SET superseded_by=NULL WHERE local_date BETWEEN ? AND ?",
                     (start_date, end_date))
        conn.execute("UPDATE exercise_sessions SET superseded_by=NULL WHERE local_date BETWEEN ? AND ?",
                     (start_date, end_date))
        for metric, tol in SAMPLE_TOLERANCE.items():
            rows = conn.execute(
                "SELECT record_id, provider, start_utc, value_num FROM measurements WHERE metric=? AND"
                " local_date BETWEEN ? AND ? AND deleted_at IS NULL ORDER BY start_utc", (metric, start_date, end_date)
            ).fetchall()
            if len({r[1] for r in rows}) < 2:
                continue
            parsed = [(r[0], r[1], datetime.fromisoformat(r[2].replace("Z", "+00:00")), r[3]) for r in rows]
            j0 = 0
            superseded: dict[str, str] = {}
            for i, (rid, prov, t, v) in enumerate(parsed):
                while parsed[j0][2] < t - timedelta(seconds=60):
                    j0 += 1
                for j in range(j0, i):
                    rid2, prov2, t2, v2 = parsed[j]
                    if prov2 == prov or rid2 in superseded or rid in superseded:
                        continue
                    if v is None or v2 is None or abs(v - v2) > tol:
                        continue
                    loser, winner = (rid, rid2) if prio.get(prov, 99) > prio.get(prov2, 99) else (rid2, rid)
                    superseded[loser] = winner
            conn.executemany("UPDATE measurements SET superseded_by=? WHERE record_id=?",
                             [(w, l) for l, w in superseded.items()])
            stats[metric] += len(superseded)
        for table in ("sleep_sessions", "exercise_sessions"):
            rows = conn.execute(
                f"SELECT record_id, provider, start_utc, end_utc FROM {table} WHERE local_date BETWEEN ? AND ?"
                " AND deleted_at IS NULL ORDER BY start_utc", (start_date, end_date)).fetchall()
            sup: dict[str, str] = {}
            parsed = [(r[0], r[1], datetime.fromisoformat(r[2].replace("Z", "+00:00")),
                       datetime.fromisoformat(r[3].replace("Z", "+00:00"))) for r in rows]
            for i in range(len(parsed)):
                for j in range(i + 1, len(parsed)):
                    a, b = parsed[i], parsed[j]
                    if b[2] >= a[3]:
                        break
                    if a[1] == b[1] or a[0] in sup or b[0] in sup:
                        continue
                    overlap = (min(a[3], b[3]) - max(a[2], b[2])).total_seconds()
                    shorter = min((a[3] - a[2]).total_seconds(), (b[3] - b[2]).total_seconds())
                    if shorter > 0 and overlap / shorter >= 0.5:
                        loser, winner = (a[0], b[0]) if prio.get(a[1], 99) > prio.get(b[1], 99) else (b[0], a[0])
                        sup[loser] = winner
            conn.executemany(f"UPDATE {table} SET superseded_by=? WHERE record_id=?", [(w, l) for l, w in sup.items()])
            stats[table] += len(sup)
    return dict(stats)
