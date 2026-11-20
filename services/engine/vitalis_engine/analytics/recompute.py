"""Recompute orchestrator: quality flags -> dedupe -> features -> sleep extras -> scores -> insights."""

from __future__ import annotations

from datetime import date, timedelta

from ..db.connection import Database, utcnow
from ..ingest.pipeline import resolve_duplicates
from ..quality.checks import flag_outliers
from . import features, insights, scores, sleep


def data_date_range(db: Database) -> tuple[date, date] | None:
    r = db.query_one("SELECT min(d) AS lo, max(d) AS hi FROM (SELECT min(local_date) AS d FROM measurements UNION ALL"
                     " SELECT max(local_date) FROM measurements UNION ALL SELECT min(local_date) FROM sleep_sessions"
                     " UNION ALL SELECT max(local_date) FROM sleep_sessions UNION ALL SELECT min(local_date) FROM"
                     " journal_entries UNION ALL SELECT max(local_date) FROM journal_entries)")
    if not r or not r["lo"]:
        return None
    return date.fromisoformat(r["lo"]), date.fromisoformat(r["hi"])


def recompute(db: Database, start: date | None = None, end: date | None = None, today: date | None = None,
              progress=None) -> dict:
    rng = data_date_range(db)
    if rng is None:
        return {"status": "no_data"}
    lo, hi = rng
    start = max(start or lo, lo) - timedelta(days=1)
    end = max(end or hi, hi)
    today = today or end

    def step(name):
        if progress:
            progress(name)

    step("quality")
    flagged = flag_outliers(db, start, end)
    dups = resolve_duplicates(db, start.isoformat(), end.isoformat())
    step("features")
    res = features.compute_features(db, start, end)
    n_feat = features.store_features(db, res, start, end)
    step("sleep")
    n_sleep = sleep.store_sleep_extras(db, start, end)
    step("scores")
    n_rec = scores.compute_recovery(db, start, end)
    n_load = scores.compute_load(db, start, end)
    step("insights")
    items = insights.generate(db, today)
    insights.store(db, items)
    from .. import ALGORITHM_VERSIONS
    for comp, ver in ALGORITHM_VERSIONS.items():
        db.execute("INSERT INTO analysis_versions(component, version, updated_at) VALUES (?,?,?) ON CONFLICT(component)"
                   " DO UPDATE SET version=excluded.version, updated_at=excluded.updated_at", (comp, ver, utcnow()))
    return {"status": "ok", "range": [start.isoformat(), end.isoformat()], "features": n_feat, "sleep_extras": n_sleep,
            "recovery_days": n_rec, "load_days": n_load, "insights": len(items), "outliers_flagged": flagged,
            "duplicates_superseded": dups}
