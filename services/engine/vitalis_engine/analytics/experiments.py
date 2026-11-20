"""Personal N-of-1 experiments (nof1.v1): AB or randomized-block designs with permutation tests."""

from __future__ import annotations

import json
import uuid
from datetime import date, timedelta

import numpy as np
import pandas as pd

from .. import ALGORITHM_VERSIONS
from ..db.connection import Database, utcnow
from .data import load_features
from .stats import apply_condition, bootstrap_diff_ci, hedges_g

VERSION = ALGORITHM_VERSIONS["experiments"]
MIN_PER_ARM = 7


def make_schedule(start: date, baseline_days: int, intervention_days: int, design: str, block_days: int,
                  seed: int) -> dict[str, str]:
    sched: dict[str, str] = {}
    d = start
    for _ in range(baseline_days):
        sched[d.isoformat()] = "baseline"
        d += timedelta(days=1)
    if design == "ab":
        for _ in range(intervention_days):
            sched[d.isoformat()] = "intervention"
            d += timedelta(days=1)
        return sched
    if design != "randomized_blocks":
        raise ValueError("design must be 'ab' or 'randomized_blocks'")
    rng = np.random.default_rng(seed)
    n_blocks = max(2, int(np.ceil(intervention_days / block_days)))
    # balanced: equal numbers of intervention/control blocks, order randomized in pairs
    labels = []
    for _ in range((n_blocks + 1) // 2):
        pair = ["intervention", "control"]
        rng.shuffle(pair)
        labels.extend(pair)
    for lab in labels[:n_blocks]:
        for _ in range(block_days):
            sched[d.isoformat()] = lab
            d += timedelta(days=1)
    return sched


def create(db: Database, spec: dict) -> dict:
    eid = str(uuid.uuid4())
    seed = int(spec.get("seed") or np.random.SeedSequence().entropy % (2 ** 31))
    start = date.fromisoformat(spec["start_date"])
    sched = make_schedule(start, int(spec["baseline_days"]), int(spec["intervention_days"]), spec["design"],
                          int(spec.get("block_days", 2)), seed)
    now = utcnow()
    db.execute("INSERT INTO experiments(id, title, hypothesis, habit_key, condition, outcome, lag_days, design,"
               " start_date, baseline_days, intervention_days, block_days, seed, schedule, status, created_at,"
               " updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (eid, spec["title"], spec["hypothesis"], spec["habit_key"], json.dumps(spec.get("condition") or {}),
                spec["outcome"], int(spec.get("lag_days", 0)), spec["design"], start.isoformat(),
                int(spec["baseline_days"]), int(spec["intervention_days"]), int(spec.get("block_days", 2)), seed,
                json.dumps(sched), "planned", now, now))
    return get(db, eid)


def get(db: Database, eid: str) -> dict:
    r = db.query_one("SELECT * FROM experiments WHERE id=?", (eid,))
    if not r:
        raise KeyError("experiment not found")
    r["condition"] = json.loads(r["condition"]) if r["condition"] else None
    r["schedule"] = json.loads(r["schedule"]) if r["schedule"] else {}
    return r


def list_all(db: Database) -> list[dict]:
    return [get(db, r["id"]) for r in db.query("SELECT id FROM experiments ORDER BY created_at DESC")]


def analyze(db: Database, eid: str, today: date | None = None, n_perm: int = 10000) -> dict:
    e = get(db, eid)
    sched = {date.fromisoformat(k): v for k, v in e["schedule"].items()}
    today = today or date.today()
    days = sorted(d for d in sched if d <= today)
    if not days:
        return {"experiment": e, "status": "not_started"}
    habit_feature = f"j:{e['habit_key']}"
    f = load_features(db, [habit_feature, e["outcome"]], days[0], days[-1] + timedelta(days=e["lag_days"]))
    x = f[habit_feature]
    did = apply_condition(x, e["condition"] or None)
    y = f[e["outcome"]].shift(-e["lag_days"])
    rows = []
    for d in days:
        arm = sched[d]
        planned_on = arm == "intervention"
        logged = None if pd.isna(did.get(d, np.nan)) else bool(did[d])
        adherent = None if logged is None or arm == "baseline" else (logged == planned_on)
        rows.append({"date": d.isoformat(), "arm": arm, "habit_condition_met": logged, "adherent": adherent,
                     "outcome": None if pd.isna(y.get(d, np.nan)) else float(y[d])})
    df = pd.DataFrame(rows)
    rated = df[df["adherent"].notna()]
    adherence = float(rated["adherent"].mean()) if len(rated) else None
    # per-protocol: use days where the plan was followed; intention-to-treat uses assigned arm
    if e["design"] == "ab":
        a = df[(df["arm"] == "intervention") & df["outcome"].notna()]["outcome"].to_numpy()
        b = df[(df["arm"] == "baseline") & df["outcome"].notna()]["outcome"].to_numpy()
        comparison = "intervention vs baseline (AB design: time trends can bias this comparison)"
    else:
        a = df[(df["arm"] == "intervention") & df["outcome"].notna()]["outcome"].to_numpy()
        b = df[(df["arm"] == "control") & df["outcome"].notna()]["outcome"].to_numpy()
        comparison = "intervention vs control blocks (randomized, intention-to-treat)"
    out = {"experiment": e, "days": rows, "adherence_rate": adherence, "n_intervention": int(len(a)),
           "n_comparison": int(len(b)), "comparison": comparison, "algorithm": VERSION}
    if len(a) < MIN_PER_ARM or len(b) < MIN_PER_ARM:
        out.update(status="insufficient_data",
                   conclusion=f"Not enough days yet ({len(a)} vs {len(b)}; need {MIN_PER_ARM} per arm).")
        return out
    diff = float(a.mean() - b.mean())
    rng = np.random.default_rng(int(e["seed"]))
    pooled = np.concatenate([a, b])
    if e["design"] == "randomized_blocks":
        # permute block labels (respecting the randomization unit)
        blocks = {}
        for r_ in rows:
            if r_["arm"] in ("intervention", "control") and r_["outcome"] is not None:
                k = (date.fromisoformat(r_["date"]) - date.fromisoformat(e["start_date"])).days - e["baseline_days"]
                blocks.setdefault(k // e["block_days"], []).append((r_["arm"], r_["outcome"]))
        keys = list(blocks)
        arms = np.array([blocks[k][0][0] == "intervention" for k in keys])
        vals = [np.array([v for _, v in blocks[k]]) for k in keys]
        count = 0
        for _ in range(n_perm):
            perm = rng.permutation(arms)
            ia = np.concatenate([vals[i] for i in range(len(keys)) if perm[i]] or [np.array([])])
            ib = np.concatenate([vals[i] for i in range(len(keys)) if not perm[i]] or [np.array([])])
            if len(ia) and len(ib) and abs(ia.mean() - ib.mean()) >= abs(diff) - 1e-12:
                count += 1
    else:
        count = 0
        for _ in range(n_perm):
            p = rng.permutation(pooled)
            if abs(p[:len(a)].mean() - p[len(a):].mean()) >= abs(diff) - 1e-12:
                count += 1
    p_perm = (count + 1) / (n_perm + 1)
    ci = bootstrap_diff_ci(a, b, seed=int(e["seed"]))
    g, gci = hedges_g(a, b)
    inconclusive = ci[0] <= 0 <= ci[1]
    out.update(status="analyzed", mean_intervention=float(a.mean()), mean_comparison=float(b.mean()),
               difference=diff, difference_ci=list(ci), p_permutation=p_perm, hedges_g=g, hedges_g_ci=list(gci),
               conclusion=("Inconclusive: the 95% interval includes no difference." if inconclusive else
                           f"In this experiment, {e['outcome']} averaged {diff:+.1f} on intervention days "
                           f"(95% CI {ci[0]:+.1f} to {ci[1]:+.1f})."),
               limitations=["Single-person experiment; results apply to you during this period only.",
                            "Unblinded: expectations can influence behaviour and self-reports.",
                            "Low adherence weakens the comparison." if adherence is not None and adherence < 0.8
                            else "Adherence was adequate."])
    return out


def update_status(db: Database, eid: str, status: str) -> dict:
    if status not in ("planned", "running", "completed", "cancelled"):
        raise ValueError("invalid status")
    db.execute("UPDATE experiments SET status=?, updated_at=? WHERE id=?", (status, utcnow(), eid))
    return get(db, eid)


def delete(db: Database, eid: str) -> None:
    db.execute("DELETE FROM experiments WHERE id=?", (eid,))
