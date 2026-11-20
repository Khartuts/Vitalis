"""Read-only analytics tools exposed to the AI analyst.

Security properties:
  * Every argument is validated by a Pydantic model (unknown fields rejected, enums for feature names).
  * Time ranges are limited (MAX_RANGE_DAYS) and results are size-limited (MAX_POINTS).
  * No SQL, no code execution, no writes. Private journal habits are hidden from cloud providers.
  * Every result carries `evidence` items with stable IDs; answers cite them as [[ev:<id>]].
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ..analytics import experiments, intraday, sleep, stats
from ..analytics.baseline import baseline_for
from ..analytics.data import load_exercises, load_features, load_measurements
from ..analytics.features import FEATURE_INFO, feature_info
from ..analytics.scores import get_scores
from ..db.connection import Database
from ..journal import list_habits, private_habit_keys
from ..quality.checks import coverage

MAX_RANGE_DAYS = 400
MAX_POINTS = 2000


class ToolError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _d(s: str) -> date:
    try:
        return date.fromisoformat(s)
    except Exception as exc:
        raise ValueError("dates must be YYYY-MM-DD") from exc


class Range(_Args):
    start: str = Field(description="Start date YYYY-MM-DD (inclusive, local calendar day)")
    end: str = Field(description="End date YYYY-MM-DD (inclusive)")

    @field_validator("start", "end")
    @classmethod
    def _v(cls, v):
        _d(v)
        return v

    def dates(self) -> tuple[date, date]:
        a, b = _d(self.start), _d(self.end)
        if b < a:
            raise ValueError("end must not be before start")
        if (b - a).days + 1 > MAX_RANGE_DAYS:
            raise ValueError(f"range too long (max {MAX_RANGE_DAYS} days)")
        return a, b


class NoArgs(_Args):
    pass


class MetricHistoryArgs(Range):
    metric: str = Field(description="Feature name from get_available_metrics (e.g. 'rhr', 'sleep_tst_min')")
    resolution: Literal["daily", "weekly", "raw"] = Field("daily", description="'raw' only for ranges ≤ 3 days")


class DayArgs(_Args):
    date: str = Field(description="Local date YYYY-MM-DD")

    @field_validator("date")
    @classmethod
    def _v(cls, v):
        _d(v)
        return v


class BaselineArgs(DayArgs):
    metric: str


class ComparePeriodsArgs(_Args):
    metric: str
    a_start: str
    a_end: str
    b_start: str
    b_end: str


class CorrelationArgs(Range):
    x: str
    y: str
    lag_days: int | None = Field(None, ge=0, le=7, description="Outcome measured lag_days after x. Default: 1 when y "
                                 "is overnight (sleep, resting HR, HRV, recovery) and x is a daytime measure, else 0.")
    method: Literal["spearman", "pearson"] = "spearman"


class Condition(_Args):
    op: Literal[">", ">=", "<", "<=", "==", "!=", "after", "before"]
    value: float | str = Field(description="Number, or HH:MM for time habits")


class HabitArgs(Range):
    habit: str = Field(description="Journal habit feature 'j:<key>'")
    outcome: str
    condition: Condition | None = None
    lag_days: int | None = Field(None, ge=0, le=7)
    adjust_for: list[str] = Field(default_factory=list, max_length=4)


class ExperimentArgs(_Args):
    experiment_id: str = Field(max_length=64)


class ChartArgs(Range):
    metrics: list[str] = Field(min_length=1, max_length=4)
    chart_type: Literal["line", "bar", "scatter"] = "line"
    title: str | None = Field(None, max_length=120)


class ReportArgs(Range):
    pass


def _features_catalog(db: Database, cloud: bool) -> dict[str, dict]:
    cat = {k: v for k, v in FEATURE_INFO.items()}
    priv = private_habit_keys(db) if cloud else set()
    for h in list_habits(db):
        if h["key"] in priv or not h["active"] or h["value_type"] in ("text", "category"):
            continue
        cat[f"j:{h['key']}"] = {"label": h["label"], "unit": h["unit"] or (h["value_type"]), "group": "journal",
                                "provenance": "user_entered", "value_type": h["value_type"]}
    cat["recovery"] = {"label": "Morning recovery estimate (relative index)", "unit": "index", "group": "recovery"}
    return cat


class Toolbox:
    def __init__(self, db: Database, cloud: bool):
        self.db = db
        self.cloud = cloud
        self.catalog = _features_catalog(db, cloud)
        self.private = private_habit_keys(db) if cloud else set()

    # ---- helpers -------------------------------------------------------------------------
    def _metric(self, m: str) -> str:
        if m.startswith("j:") and m[2:] in self.private:
            raise ToolError("private_habit", f"'{m}' is a private habit and is excluded from cloud AI requests.")
        if m not in self.catalog:
            raise ToolError("unknown_metric", f"Unknown metric '{m}'. Call get_available_metrics for valid names.")
        return m

    def _series(self, m: str, a: date, b: date) -> pd.Series:
        if m == "recovery":
            rows = get_scores(self.db, "recovery", a, b)
            s = pd.Series({date.fromisoformat(r["local_date"]): r["value"] for r in rows}, dtype=float)
            return s.reindex(pd.date_range(a, b).date)
        return load_features(self.db, [m], a, b)[m]

    @staticmethod
    def _summary(s: pd.Series) -> dict:
        v = s.dropna()
        if v.empty:
            return {"n": 0, "missing_days": int(len(s))}
        return {"n": int(len(v)), "missing_days": int(len(s) - len(v)), "mean": float(v.mean()),
                "median": float(v.median()), "min": float(v.min()), "max": float(v.max()),
                "sd": float(v.std(ddof=1)) if len(v) > 1 else None,
                "first_date": v.index[0].isoformat(), "last_date": v.index[-1].isoformat()}

    def _ev(self, metric: str, a: date, b: date, summary: dict | None = None, kind: str = "feature_series") -> dict:
        info = self.catalog.get(metric, feature_info(metric))
        return {"id": f"{metric}|{a.isoformat()}|{b.isoformat()}", "kind": kind, "metric": metric,
                "label": info.get("label", metric), "unit": info.get("unit"), "start": a.isoformat(),
                "end": b.isoformat(), "n": (summary or {}).get("n")}

    def _scrub(self, obj: Any) -> Any:
        """Remove private habit values from nested results for cloud providers."""
        if not self.private:
            return obj
        if isinstance(obj, dict):
            return {k: self._scrub(v) for k, v in obj.items() if not (isinstance(k, str) and k.startswith("j:")
                                                                        and k[2:] in self.private)}
        if isinstance(obj, list):
            return [self._scrub(x) for x in obj]
        return obj

    # ---- tools -----------------------------------------------------------------------------
    def get_available_metrics(self, args: NoArgs) -> dict:
        rows = self.db.query("SELECT feature, count(*) AS n, min(local_date) AS first, max(local_date) AS last FROM"
                             " daily_features GROUP BY feature")
        have = {r["feature"]: r for r in rows}
        out = []
        for k, info in self.catalog.items():
            r = have.get(k)
            if k == "recovery":
                rr = self.db.query_one("SELECT count(*) AS n, min(local_date) AS first, max(local_date) AS last FROM"
                                       " scores WHERE kind='recovery' AND value IS NOT NULL")
                r = rr if rr and rr["n"] else None
            out.append({"metric": k, "label": info["label"], "unit": info.get("unit"), "group": info.get("group"),
                        "provenance": info.get("provenance", "calculated"), "available": bool(r),
                        "days_with_data": r["n"] if r else 0, "first": r["first"] if r else None,
                        "last": r["last"] if r else None})
        sources = self.db.query("SELECT provider, app_package, device_type, device_manufacturer, device_model FROM"
                                " sources")
        return {"metrics": out, "sources": sources, "demo_profile": bool(self.db.get_pref("is_demo", False)),
                "note": "Only metrics with available=true have data. Never state values for unavailable metrics.",
                "private_habits_hidden": len(self.private)}

    def get_metric_history(self, args: MetricHistoryArgs) -> dict:
        a, b = args.dates()
        m = self._metric(args.metric)
        s = self._series(m, a, b)
        summ = self._summary(s)
        if args.resolution == "raw":
            if (b - a).days > 2:
                raise ToolError("range_too_long", "raw resolution is limited to 3 days")
            raw_metric = {"rhr": "heart_rate", "hr_day_mean": "heart_rate", "hrv_night_rmssd": "hrv_rmssd",
                          "spo2_night_mean": "spo2"}.get(m)
            if not raw_metric:
                raise ToolError("no_raw", f"no raw sample series for '{m}'")
            df = load_measurements(self.db, [raw_metric], a, b)
            pts = [{"t": t.isoformat(), "v": float(v)} for t, v in zip(df["start"], df["value"])][:MAX_POINTS]
            return {"metric": raw_metric, "points": pts, "truncated": len(df) > MAX_POINTS, "summary": summ,
                    "evidence": [self._ev(raw_metric, a, b, summ, "raw_samples")]}
        if args.resolution == "weekly":
            ser = s.copy()
            ser.index = pd.to_datetime(ser.index)
            w = ser.resample("W-MON", label="left", closed="left").agg(["mean", "count"])
            pts = [{"week_start": i.date().isoformat(), "mean": None if pd.isna(r["mean"]) else float(r["mean"]),
                    "days": int(r["count"])} for i, r in w.iterrows()]
        else:
            pts = [{"date": d.isoformat(), "value": None if pd.isna(v) else round(float(v), 2)} for d, v in s.items()]
        return {"metric": m, "label": self.catalog[m]["label"], "unit": self.catalog[m].get("unit"),
                "provenance": self.catalog[m].get("provenance", "calculated"), "resolution": args.resolution,
                "points": pts[:MAX_POINTS], "summary": summ, "evidence": [self._ev(m, a, b, summ)]}

    def get_sleep_summary(self, args: Range) -> dict:
        a, b = args.dates()
        cols = ["sleep_tst_min", "sleep_tib_min", "sleep_efficiency", "sleep_bedtime_min", "sleep_wake_min",
                "sleep_latency_min", "sleep_waso_min", "sleep_awakenings", "sleep_deep_min", "sleep_rem_min",
                "sleep_light_min", "nap_min", "sleep_score_imported", "sleep_sri_14d", "sleep_debt_min"]
        f = load_features(self.db, cols, a, b)
        nights = []
        for d, r in f.iterrows():
            if pd.isna(r["sleep_tst_min"]):
                continue
            nights.append({"date": d.isoformat(), **{c: (None if pd.isna(r[c]) else round(float(r[c]), 1))
                                                       for c in cols}})
        summaries = {c: self._summary(f[c]) for c in cols}
        return {"period": [a.isoformat(), b.isoformat()], "nights_recorded": len(nights),
                "nights": nights[-120:], "summary": summaries, "consistency": sleep.consistency(self.db, b, (b - a).days + 1),
                "notes": ["Sleep is dated by wake-up day.", "Times are minutes from local midnight (negative = before "
                          "midnight).", "Wrist-derived sleep stages are estimates, not clinical polysomnography.",
                          "sleep_debt_min assumes the configured sleep need and an exponential decay of 0.85/night."],
                "evidence": [self._ev("sleep_tst_min", a, b, summaries["sleep_tst_min"]),
                             self._ev("sleep_efficiency", a, b, summaries["sleep_efficiency"])]}

    def get_recovery_summary(self, args: Range) -> dict:
        a, b = args.dates()
        rows = get_scores(self.db, "recovery", a, b)
        days = [{"date": r["local_date"], "index": None if r["value"] is None else round(r["value"], 1),
                 "z": None if r["z"] is None else round(r["z"], 2), "category": r["category"],
                 "completeness": r["completeness"], "baseline_status": r["baseline_status"],
                 "drivers": [{k: u.get(k) for k in ("component", "feature", "value", "baseline_median", "z",
                                                    "contribution")} for u in r["detail"]["inputs_used"]],
                 "missing": r["detail"]["inputs_missing"]} for r in rows]
        vals = pd.Series([d["index"] for d in days], dtype=float)
        return {"period": [a.isoformat(), b.isoformat()], "algorithm": rows[0]["algo_version"] if rows else None,
                "days": days[-60:], "summary": self._summary(pd.Series(vals.values, index=[date.fromisoformat(
                    d["date"]) for d in days])) if days else {"n": 0},
                "interpretation": "Relative index: 50 = your usual; ±20 ≈ one robust SD of your own history. Not a "
                                  "medical measure.", "evidence": [self._ev("recovery", a, b, {"n": len(days)})]}

    def get_stress_timeline(self, args: DayArgs) -> dict:
        d = _d(args.date)
        t = intraday.arousal_timeline(self.db, d)
        elevated = [p for p in t["points"] if p["state"] == "elevated_low_movement"]
        periods, cur = [], None
        for p in t["points"]:
            if p["state"] == "elevated_low_movement":
                if cur is None:
                    cur = {"start": p["t"], "end": p["t"], "max_elevation": p["elevation"]}
                cur["end"] = p["t"]
                cur["max_elevation"] = max(cur["max_elevation"] or 0, p["elevation"] or 0)
            elif cur:
                periods.append(cur)
                cur = None
        if cur:
            periods.append(cur)
        journal = {}
        if "stress" not in self.private and "mood" not in self.private:
            f = load_features(self.db, ["j:stress", "j:mood"], d, d)
            journal = {k: (None if pd.isna(v) else float(v)) for k, v in f.iloc[0].items()}
        return {"date": d.isoformat(), "status": t["status"], "summary": t["summary"], "elevated_periods": periods[:40],
                "elevated_bins_15min": len(elevated), "self_reported": journal, "missing_inputs": t["missing_inputs"],
                "interpretation_note": t["interpretation_note"],
                "evidence": [self._ev("arousal_min", d, d, {"n": len(t["points"])}, "intraday_timeline")]}

    def get_daily_activity(self, args: Range) -> dict:
        a, b = args.dates()
        cols = ["steps", "active_kcal", "distance_m", "exercise_min", "exercise_count", "inactive_hours", "trimp",
                "hr_z1_min", "hr_z2_min", "hr_z3_min", "hr_z4_min", "hr_z5_min"]
        f = load_features(self.db, cols, a, b)
        ex = load_exercises(self.db, a, b)
        sessions = [{"date": r.local_date, "type": r.exercise_type, "minutes": round((r.end - r.start).total_seconds()
                                                                                   / 60, 1)} for r in ex.itertuples()]
        load = get_scores(self.db, "load", b, b)
        return {"period": [a.isoformat(), b.isoformat()], "summary": {c: self._summary(f[c]) for c in cols},
                "days": [{"date": d.isoformat(), **{c: (None if pd.isna(r[c]) else round(float(r[c]), 1))
                                                    for c in cols}} for d, r in f.iterrows()][-120:],
                "exercise_sessions": sessions[-100:], "load_today": load[0]["detail"] if load else None,
                "notes": ["Training load is TRIMP from heart-rate zones; needs max HR (set or age-estimated)."],
                "evidence": [self._ev("steps", a, b, self._summary(f["steps"])),
                             self._ev("trimp", a, b, self._summary(f["trimp"]))]}

    def get_baseline(self, args: BaselineArgs) -> dict:
        m = self._metric(args.metric)
        d = _d(args.date)
        b = baseline_for(self.db, m, d)
        return {"baseline": b.as_dict(), "evidence": [self._ev(m, d - timedelta(days=b.window_days),
                                                               d - timedelta(days=1), {"n": b.n}, "baseline")]}

    def compare_periods(self, args: ComparePeriodsArgs) -> dict:
        m = self._metric(args.metric)
        ra = Range(start=args.a_start, end=args.a_end).dates()
        rb = Range(start=args.b_start, end=args.b_end).dates()
        sa, sb = self._series(m, *ra).dropna(), self._series(m, *rb).dropna()
        out = {"metric": m, "label": self.catalog[m]["label"], "unit": self.catalog[m].get("unit"),
               "period_a": {"start": args.a_start, "end": args.a_end, **self._summary(self._series(m, *ra))},
               "period_b": {"start": args.b_start, "end": args.b_end, **self._summary(self._series(m, *rb))}}
        if len(sa) >= 5 and len(sb) >= 5:
            ci = stats.bootstrap_diff_ci(sa.to_numpy(float), sb.to_numpy(float))
            g, gci = stats.hedges_g(sa.to_numpy(float), sb.to_numpy(float))
            out.update(difference_mean=float(sa.mean() - sb.mean()), difference_ci95=list(ci), hedges_g=g,
                       verdict="inconclusive" if ci[0] <= 0 <= ci[1] else "difference_detected")
        else:
            out["verdict"] = "insufficient_data"
        out["evidence"] = [self._ev(m, *ra, out["period_a"]), self._ev(m, *rb, out["period_b"])]
        return out

    def calculate_correlation(self, args: CorrelationArgs) -> dict:
        a, b = args.dates()
        x, y = self._metric(args.x), self._metric(args.y)
        lag = stats.default_lag(y, x) if args.lag_days is None else args.lag_days
        sx = self._series(x, a, b)
        sy = self._series(y, a, b + timedelta(days=lag)).shift(-lag).loc[:b]
        r = stats.correlate(sx, sy, args.method)
        r.update(x=x, y=y, lag_days=lag, evidence=[self._ev(x, a, b), self._ev(y, a, b + timedelta(days=lag))])
        return r

    def analyze_habit(self, args: HabitArgs) -> dict:
        a, b = args.dates()
        h, y = self._metric(args.habit), self._metric(args.outcome)
        if not h.startswith("j:"):
            raise ToolError("not_a_habit", "habit must be a journal habit 'j:<key>'")
        adj = [self._metric(c) for c in args.adjust_for]
        res = stats.habit_analysis(self.db, h, y, a, b, args.lag_days,
                                   args.condition.model_dump() if args.condition else None, adj or None)
        res["evidence"] = [self._ev(h, a, b), self._ev(y, a, b)]
        return res

    def run_experiment_analysis(self, args: ExperimentArgs) -> dict:
        try:
            res = experiments.analyze(self.db, args.experiment_id, n_perm=5000)
        except KeyError:
            raise ToolError("not_found", "experiment not found")
        if self.cloud and res["experiment"]["habit_key"] in self.private:
            raise ToolError("private_habit", "This experiment uses a private habit.")
        res.pop("days", None)
        e = res["experiment"]
        res["evidence"] = [{"id": f"experiment|{e['id']}", "kind": "experiment", "label": e["title"]}]
        return res

    def get_data_quality(self, args: Range) -> dict:
        a, b = args.dates()
        c = coverage(self.db, a, b)
        c.pop("daily", None)
        for v in c["per_metric"].values():
            v["missing_dates"] = v["missing_dates"][-20:]
        last = self.db.query("SELECT provider, import_method, finished_at, status FROM import_batches ORDER BY"
                             " started_at DESC LIMIT 5")
        return {"period": [a.isoformat(), b.isoformat()], "coverage": c, "recent_imports": last,
                "evidence": [{"id": f"quality|{a.isoformat()}|{b.isoformat()}", "kind": "data_quality",
                              "label": "Data coverage", "start": a.isoformat(), "end": b.isoformat()}]}

    def generate_chart_spec(self, args: ChartArgs) -> dict:
        a, b = args.dates()
        ms = [self._metric(m) for m in args.metrics]
        spec = {"type": args.chart_type, "start": a.isoformat(), "end": b.isoformat(), "title": args.title,
                "series": [{"metric": m, "label": self.catalog[m]["label"], "unit": self.catalog[m].get("unit")}
                           for m in ms]}
        return {"chart": spec, "note": "The app renders this chart from local data; do not invent data points.",
                "evidence": [self._ev(m, a, b) for m in ms]}

    def create_report(self, args: ReportArgs) -> dict:
        a, b = args.dates()
        return {"report": {"start": a.isoformat(), "end": b.isoformat()},
                "note": "The user can open and export this report from the Reports screen.",
                "evidence": [{"id": f"report|{a.isoformat()}|{b.isoformat()}", "kind": "report",
                              "label": "Health report", "start": a.isoformat(), "end": b.isoformat()}]}


TOOLS: dict[str, tuple[type[_Args], str]] = {
    "get_available_metrics": (NoArgs, "List metrics/features with data availability and data sources. Call first."),
    "get_metric_history": (MetricHistoryArgs, "Daily (or weekly/raw) values and summary statistics for one metric."),
    "get_sleep_summary": (Range, "Nightly sleep metrics, summaries, consistency, regularity index and sleep debt."),
    "get_recovery_summary": (Range, "Morning recovery estimates with drivers, missing inputs and baseline status."),
    "get_stress_timeline": (DayArgs, "Intraday low-movement heart-rate elevation timeline for one day, plus "
                                     "self-reported stress/mood if allowed. Not a psychological stress measure."),
    "get_daily_activity": (Range, "Steps, energy, exercise sessions, HR zones, TRIMP load."),
    "get_baseline": (BaselineArgs, "Personal robust baseline (median, robust SD, status) for a metric on a date."),
    "compare_periods": (ComparePeriodsArgs, "Compare a metric between two date ranges with bootstrap CI."),
    "calculate_correlation": (CorrelationArgs, "Correlation between two daily metrics with CI and effective N."),
    "analyze_habit": (HabitArgs, "Compare outcome on days a journal habit condition was met vs not, with effect "
                                 "sizes, CIs and optional covariate adjustment."),
    "run_experiment_analysis": (ExperimentArgs, "Analyze a user N-of-1 experiment."),
    "get_data_quality": (Range, "Data coverage/completeness and recent imports."),
    "generate_chart_spec": (ChartArgs, "Ask the app to show a chart of 1–4 metrics (rendered from local data)."),
    "create_report": (ReportArgs, "Prepare a health report for a date range in the Reports screen."),
}


def tool_definitions(db: Database, cloud: bool) -> list[dict]:
    """JSON-schema tool definitions; metric parameters are constrained to the live catalog via enum."""
    cat = sorted(_features_catalog(db, cloud).keys())
    habits = [m for m in cat if m.startswith("j:")]
    defs = []
    for name, (model, desc) in TOOLS.items():
        schema = model.model_json_schema()
        schema.pop("title", None)
        props = schema.get("properties", {})
        for p in ("metric", "x", "y", "outcome"):
            if p in props:
                props[p] = {**props[p], "enum": cat}
        if "habit" in props:
            props["habit"] = {**props["habit"], "enum": habits or ["j:none"]}
        if "metrics" in props:
            props["metrics"]["items"] = {"type": "string", "enum": cat}
        if "adjust_for" in props:
            props["adjust_for"]["items"] = {"type": "string", "enum": cat}
        for v in props.values():
            v.pop("title", None)
        schema["additionalProperties"] = False
        defs.append({"name": name, "description": desc, "parameters": _inline_refs(schema)})
    return defs


def _inline_refs(schema: dict) -> dict:
    defs = schema.pop("$defs", {})

    def walk(o):
        if isinstance(o, dict):
            if "$ref" in o:
                return walk(defs[o["$ref"].split("/")[-1]])
            return {k: walk(v) for k, v in o.items() if k != "title"}
        if isinstance(o, list):
            return [walk(x) for x in o]
        return o
    return walk(schema)


def call_tool(db: Database, name: str, arguments: dict | str, cloud: bool) -> dict:
    if name not in TOOLS:
        return {"ok": False, "error": {"code": "unknown_tool", "message": f"No tool named {name}"}}
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments or "{}")
        except json.JSONDecodeError:
            return {"ok": False, "error": {"code": "invalid_json", "message": "arguments are not valid JSON"}}
    model, _ = TOOLS[name]
    try:
        args = model.model_validate(arguments or {})
        box = Toolbox(db, cloud)
        if isinstance(args, Range):
            args.dates()
        result = getattr(box, name)(args)
        result = box._scrub(_jsonable(result))
        size = len(json.dumps(result))
        if size > 200_000:
            return {"ok": False, "error": {"code": "result_too_large", "message": "Narrow the date range."}}
        return {"ok": True, "result": result}
    except ValidationError as exc:
        return {"ok": False, "error": {"code": "invalid_arguments",
                                       "message": "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}"
                                                            for e in exc.errors())[:800]}}
    except ToolError as exc:
        return {"ok": False, "error": {"code": exc.code, "message": str(exc)}}
    except ValueError as exc:
        return {"ok": False, "error": {"code": "invalid_arguments", "message": str(exc)}}


def _jsonable(o):
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(x) for x in o]
    if isinstance(o, (np.floating,)):
        return None if np.isnan(o) else float(o)
    if isinstance(o, float) and (np.isnan(o) or np.isinf(o)):
        return None
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, date):
        return o.isoformat()
    return o
