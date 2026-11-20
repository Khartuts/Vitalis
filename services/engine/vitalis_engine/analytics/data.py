"""Loaders that turn stored records into analysis-ready frames (excluding superseded/deleted/invalid rows)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

from ..db.connection import Database
from ..ingest.pipeline import source_priority
from ..model.canonical import ASLEEP_STAGES
from ..model.metrics import QualityFlag
from ..timeutil import DEFAULT_TZ, local_date

EXCLUDE_FLAGS = int(QualityFlag.OUT_OF_RANGE | QualityFlag.FUTURE_TIMESTAMP | QualityFlag.OUTLIER)


def _ts(col: pd.Series) -> pd.Series:
    return pd.to_datetime(col, utc=True, format="ISO8601")


def load_measurements(db: Database, metrics: list[str], start: date, end: date, include_flagged: bool = False
                      ) -> pd.DataFrame:
    q = ("SELECT record_id, provider, source_id, metric, value_num AS value, start_utc, end_utc, utc_offset_min,"
         " local_date, quality_flags, provenance, meta FROM measurements WHERE metric IN ({}) AND local_date BETWEEN ? AND ?"
         " AND deleted_at IS NULL AND superseded_by IS NULL").format(",".join("?" * len(metrics)))
    rows = db.query(q, (*metrics, start.isoformat(), end.isoformat()))
    df = pd.DataFrame(rows, columns=["record_id", "provider", "source_id", "metric", "value", "start_utc", "end_utc",
                                     "utc_offset_min", "local_date", "quality_flags", "provenance", "meta"])
    if df.empty:
        return df.assign(start=pd.Series(dtype="datetime64[ns, UTC]"), end=pd.Series(dtype="datetime64[ns, UTC]"))
    if not include_flagged:
        df = df[(df["quality_flags"].astype(int) & EXCLUDE_FLAGS) == 0]
    df = df.assign(start=_ts(df["start_utc"]), end=_ts(df["end_utc"].fillna(df["start_utc"])))
    df["value"] = df["value"].astype(float)
    return df.sort_values("start").reset_index(drop=True)


def source_app(db: Database) -> dict[int, str]:
    return {r["id"]: (r["app_package"] or r["provider"]) for r in db.query("SELECT id, provider, app_package FROM sources")}


@dataclass
class SleepEpisode:
    start: datetime
    end: datetime
    offset_min: int | None
    wake_date: date
    record_ids: list[str]
    providers: list[str]
    stages: list[tuple[str, datetime, datetime]] = field(default_factory=list)
    has_stages: bool = False
    quality_flags: int = 0
    end_offset_min: int | None = None
    end_offset_known: bool = False

    @property
    def duration_min(self) -> float:
        return (self.end - self.start).total_seconds() / 60.0

    def to_dict(self) -> dict:
        return {"start": self.start.isoformat(), "end": self.end.isoformat(), "wake_date": self.wake_date.isoformat(),
                "duration_min": round(self.duration_min, 1), "record_ids": self.record_ids,
                "providers": sorted(set(self.providers)), "has_stages": self.has_stages,
                "stages": [{"stage": s, "start": a.isoformat(), "end": b.isoformat()} for s, a, b in self.stages]}


def load_sleep_episodes(db: Database, start: date, end: date, tz: str = DEFAULT_TZ,
                        merge_gap_min: float = 60.0) -> list[SleepEpisode]:
    """Sessions (non-superseded) merged into episodes; stage-only export rows attached to their parent session."""
    lo = (start - timedelta(days=1)).isoformat()
    hi = (end + timedelta(days=1)).isoformat()
    sessions = db.query("SELECT record_id, provider, start_utc, end_utc, utc_offset_min, end_offset_min, has_stages,"
                        " meta, quality_flags"
                        " FROM sleep_sessions WHERE local_date BETWEEN ? AND ? AND deleted_at IS NULL"
                        " AND superseded_by IS NULL ORDER BY start_utc", (lo, hi))
    if not sessions:
        return []
    ids = [s["record_id"] for s in sessions]
    stages_by: dict[str, list] = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for r in db.query("SELECT session_record_id, stage, start_utc, end_utc FROM sleep_stages WHERE"
                          f" session_record_id IN ({','.join('?' * len(chunk))}) ORDER BY start_utc", chunk):
            stages_by.setdefault(r["session_record_id"], []).append(
                (r["stage"], _pt(r["start_utc"]), _pt(r["end_utc"])))
    parents, stage_only = [], []
    for s in sessions:
        meta = json.loads(s["meta"]) if s["meta"] else {}
        (stage_only if meta.get("stage_only") else parents).append(s)
    parsed = [dict(s, _s=_pt(s["start_utc"]), _e=_pt(s["end_utc"]), _st=list(stages_by.get(s["record_id"], [])))
              for s in parents]
    for so in stage_only:
        a, b = _pt(so["start_utc"]), _pt(so["end_utc"])
        for p in parsed:
            if p["_s"] - timedelta(minutes=5) <= a and b <= p["_e"] + timedelta(minutes=5):
                p["_st"].extend(stages_by.get(so["record_id"], []))
                break
    episodes: list[SleepEpisode] = []
    for p in parsed:
        if episodes and (p["_s"] - episodes[-1].end).total_seconds() / 60.0 <= merge_gap_min:
            ep = episodes[-1]
            if p["_e"] >= ep.end:
                ep.end_offset_min = p["end_offset_min"]
                ep.end_offset_known = p["end_offset_min"] is not None
                ep._last_start_offset = p["utc_offset_min"]  # type: ignore[attr-defined]
                ep._last_start = p["_s"]  # type: ignore[attr-defined]
            ep.end = max(ep.end, p["_e"])
            ep.record_ids.append(p["record_id"])
            ep.providers.append(p["provider"])
            ep.stages.extend(p["_st"])
            ep.quality_flags |= int(p["quality_flags"] or 0)
        else:
            ep = SleepEpisode(p["_s"], p["_e"], p["utc_offset_min"], date.min, [p["record_id"]],
                              [p["provider"]], list(p["_st"]), quality_flags=int(p["quality_flags"] or 0),
                              end_offset_min=p["end_offset_min"], end_offset_known=p["end_offset_min"] is not None)
            ep._last_start_offset = p["utc_offset_min"]  # type: ignore[attr-defined]
            ep._last_start = p["_s"]  # type: ignore[attr-defined]
            episodes.append(ep)
    from ..ingest.pipeline import zone_consistent_offset
    for ep in episodes:
        ep.stages.sort(key=lambda x: x[1])
        ep.has_stages = any(s[0] in ASLEEP_STAGES or s[0] == "awake" for s in ep.stages)
        if not ep.end_offset_known:
            ep.end_offset_min = zone_consistent_offset(ep._last_start, ep._last_start_offset, tz)  # type: ignore
        ep.start_offset_eff = zone_consistent_offset(ep.start, ep.offset_min, tz)  # type: ignore[attr-defined]
        ep.wake_date = local_date(ep.end, ep.end_offset_min, tz)
    return [e for e in episodes if start <= e.wake_date <= end]


def _pt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def load_exercises(db: Database, start: date, end: date) -> pd.DataFrame:
    rows = db.query("SELECT record_id, provider, exercise_type, title, start_utc, end_utc, local_date, distance_m,"
                    " active_kcal, avg_hr, max_hr FROM exercise_sessions WHERE local_date BETWEEN ? AND ?"
                    " AND deleted_at IS NULL AND superseded_by IS NULL ORDER BY start_utc",
                    (start.isoformat(), end.isoformat()))
    df = pd.DataFrame(rows, columns=["record_id", "provider", "exercise_type", "title", "start_utc", "end_utc",
                                     "local_date", "distance_m", "active_kcal", "avg_hr", "max_hr"])
    if df.empty:
        return df.assign(start=pd.Series(dtype="datetime64[ns, UTC]"), end=pd.Series(dtype="datetime64[ns, UTC]"))
    return df.assign(start=_ts(df["start_utc"]), end=_ts(df["end_utc"]))


def load_features(db: Database, features: list[str] | None, start: date, end: date) -> pd.DataFrame:
    """Wide frame indexed by date (all dates in range), one column per feature."""
    if features:
        rows = db.query("SELECT local_date, feature, value FROM daily_features WHERE feature IN ({}) AND local_date"
                        " BETWEEN ? AND ?".format(",".join("?" * len(features))),
                        (*features, start.isoformat(), end.isoformat()))
    else:
        rows = db.query("SELECT local_date, feature, value FROM daily_features WHERE local_date BETWEEN ? AND ?",
                        (start.isoformat(), end.isoformat()))
    idx = pd.date_range(start, end, freq="D").date
    if not rows:
        return pd.DataFrame(index=pd.Index(idx, name="date"), columns=features or [], dtype=float)
    df = pd.DataFrame(rows)
    wide = df.pivot_table(index="local_date", columns="feature", values="value", aggfunc="first")
    wide.index = pd.to_datetime(wide.index).date
    wide = wide.reindex(idx)
    wide.index.name = "date"
    if features:
        for f in features:
            if f not in wide.columns:
                wide[f] = np.nan
        wide = wide[features]
    return wide.astype(float)


def pick_provider_per_day(df: pd.DataFrame, db: Database) -> pd.DataFrame:
    """For summed interval metrics: keep one provider per local day (highest priority with data)."""
    if df.empty:
        return df
    prio = {p: i for i, p in enumerate(source_priority(db))}
    df = df.assign(_p=df["provider"].map(lambda p: prio.get(p, 99)))
    best = df.groupby("local_date")["_p"].transform("min")
    return df[df["_p"] == best].drop(columns="_p")


def time_weights(times: pd.Series, cap_min: float = 10.0) -> np.ndarray:
    """Minutes represented by each sample: the gap to the next sample, capped. Before a long gap (or at the end)
    a sample represents its local sampling interval (gap to the previous sample), so a dense workout series
    does not leak into the following unmeasured period."""
    if len(times) == 0:
        return np.array([])
    t = (times - times.iloc[0]).dt.total_seconds().to_numpy() / 60.0
    if len(t) == 1:
        return np.array([min(1.0, cap_min)])
    gaps = np.diff(t)
    nxt = np.append(gaps, np.inf)
    prev = np.insert(gaps, 0, np.inf)
    w = np.where(nxt <= cap_min, nxt, np.minimum(prev, cap_min))
    w = np.where(np.isinf(w), cap_min, w)
    return np.clip(w, 0, cap_min)


def utc_bounds_for_dates(start: date, end: date, tz: str = DEFAULT_TZ) -> tuple[datetime, datetime]:
    from ..timeutil import day_bounds_utc
    return day_bounds_utc(start, tz)[0], day_bounds_utc(end, tz)[1]


__all__ = ["load_measurements", "load_sleep_episodes", "load_exercises", "load_features", "pick_provider_per_day",
           "time_weights", "SleepEpisode", "utc_bounds_for_dates", "source_app", "timezone"]
