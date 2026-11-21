"""Statistics for the Correlation & Experiment Lab (stats.v1).

Design rules: report n and missing data; attach CIs; adjust for autocorrelation (effective N);
use FDR control when scanning many pairs; label results "inconclusive" unless the evidence is clear;
never phrase associations as causes.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd
from scipy import stats as sps

from .. import ALGORITHM_VERSIONS
from ..db.connection import Database
from .data import load_features
from .features import feature_info

VERSION = ALGORITHM_VERSIONS["stats"]
MIN_PAIRS = 10
SMALL_N = 20


def _lag1(x: np.ndarray) -> float:
    if len(x) < 4 or np.std(x) == 0:
        return 0.0
    return float(np.corrcoef(x[:-1], x[1:])[0, 1])


def effective_n(x: np.ndarray, y: np.ndarray) -> float:
    """Bartlett-type correction for serial correlation: n·(1−r1x·r1y)/(1+r1x·r1y)."""
    r = _lag1(x) * _lag1(y)
    r = float(np.clip(r, -0.9, 0.9))
    return max(3.0, len(x) * (1 - r) / (1 + r))


def fisher_ci(r: float, n_eff: float, alpha: float = 0.05, spearman: bool = False) -> tuple[float, float]:
    if n_eff <= 3 or abs(r) >= 1:
        return (float("nan"), float("nan"))
    z = math.atanh(r)
    se = math.sqrt((1 + r * r / 2) / (n_eff - 3)) if spearman else 1 / math.sqrt(n_eff - 3)  # Bonett–Wright
    q = sps.norm.ppf(1 - alpha / 2)
    return (math.tanh(z - q * se), math.tanh(z + q * se))


def _p_from_r(r: float, n_eff: float) -> float:
    if n_eff <= 3 or abs(r) >= 1:
        return float("nan")
    t = r * math.sqrt((n_eff - 2) / (1 - r * r))
    return float(2 * sps.t.sf(abs(t), n_eff - 2))


def strength_label(r: float) -> str:
    a = abs(r)
    return "negligible" if a < 0.1 else "weak" if a < 0.3 else "moderate" if a < 0.5 else "strong"


def verdict(ci: tuple[float, float], n: int, p: float | None) -> str:
    if n < MIN_PAIRS:
        return "insufficient_data"
    if any(map(math.isnan, ci)) or ci[0] <= 0 <= ci[1]:
        return "inconclusive"
    if n < SMALL_N:
        return "suggestive_small_sample"
    return "association_detected"


def correlate(x: pd.Series, y: pd.Series, method: str = "spearman") -> dict:
    df = pd.concat([x.rename("x"), y.rename("y")], axis=1)
    n_total = len(df)
    df = df.dropna()
    n = len(df)
    out = {"method": method, "n": n, "n_total_days": n_total, "n_missing": n_total - n, "algorithm": VERSION}
    if n < MIN_PAIRS or df["x"].nunique() < 2 or df["y"].nunique() < 2:
        out.update(r=None, ci=None, p=None, verdict="insufficient_data",
                   explanation=f"Only {n} days have both values (need at least {MIN_PAIRS}) or a variable never varies.")
        return out
    xv, yv = df["x"].to_numpy(float), df["y"].to_numpy(float)
    if method == "pearson":
        r = float(np.corrcoef(xv, yv)[0, 1])
        normal = (sps.shapiro(xv).pvalue > 0.01 and sps.shapiro(yv).pvalue > 0.01) if n <= 5000 else True
        out["assumption_note"] = None if normal else "Data look non-normal; Spearman is more appropriate."
    else:
        r = float(sps.spearmanr(xv, yv)[0])  # [0]: also works with SciPy < 1.9 (Android build)
    ne = effective_n(xv, yv)
    ci = fisher_ci(r, ne, spearman=(method == "spearman"))
    p = _p_from_r(r, ne)
    out.update(r=r, ci=list(ci), p=p, n_effective=round(ne, 1), strength=strength_label(r),
               verdict=verdict(ci, n, p),
               explanation=_explain_corr(r, ci, n, ne))
    return out


def _explain_corr(r: float, ci, n: int, ne: float) -> str:
    direction = "higher" if r > 0 else "lower"
    s = (f"Across {n} days, days with higher values of the first variable tended to have {direction} values of the "
         f"second (r = {r:.2f}, 95% CI {ci[0]:.2f} to {ci[1]:.2f}).")
    if ci[0] <= 0 <= ci[1]:
        s += " The interval includes zero, so the data are compatible with no relationship."
    if ne < n * 0.8:
        s += f" Day-to-day carry-over reduces the effective sample to about {ne:.0f} days."
    s += " This is an association in your data, not proof of cause and effect."
    return s


def hedges_g(a: np.ndarray, b: np.ndarray) -> tuple[float, tuple[float, float]]:
    na, nb = len(a), len(b)
    sp = math.sqrt(((na - 1) * a.var(ddof=1) + (nb - 1) * b.var(ddof=1)) / (na + nb - 2)) if na + nb > 2 else 0
    if sp == 0:
        return 0.0, (float("nan"), float("nan"))
    d = (a.mean() - b.mean()) / sp
    J = 1 - 3 / (4 * (na + nb) - 9)
    g = d * J
    se = math.sqrt((na + nb) / (na * nb) + g * g / (2 * (na + nb)))
    return float(g), (float(g - 1.96 * se), float(g + 1.96 * se))


def cliffs_delta(a: np.ndarray, b: np.ndarray) -> float:
    gt = sum((x > b).sum() for x in a)
    lt = sum((x < b).sum() for x in a)
    return float((gt - lt) / (len(a) * len(b)))


def bootstrap_diff_ci(a: np.ndarray, b: np.ndarray, stat=np.mean, n_boot: int = 4000, seed: int = 7
                      ) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    ia = rng.integers(0, len(a), (n_boot, len(a)))
    ib = rng.integers(0, len(b), (n_boot, len(b)))
    diffs = stat(a[ia], axis=1) - stat(b[ib], axis=1)
    return float(np.percentile(diffs, 2.5)), float(np.percentile(diffs, 97.5))


def compare_groups(values: pd.Series, mask: pd.Series, label_a: str = "with", label_b: str = "without") -> dict:
    df = pd.concat([values.rename("v"), mask.rename("m")], axis=1).dropna()
    a = df.loc[df["m"].astype(bool), "v"].to_numpy(float)
    b = df.loc[~df["m"].astype(bool), "v"].to_numpy(float)
    out = {"n_a": len(a), "n_b": len(b), "label_a": label_a, "label_b": label_b, "algorithm": VERSION}
    if len(a) < 5 or len(b) < 5:
        out.update(verdict="insufficient_data",
                   explanation=f"Need at least 5 days in each group (have {len(a)} and {len(b)}).")
        return out
    welch = sps.ttest_ind(a, b, equal_var=False)
    mw = sps.mannwhitneyu(a, b, alternative="two-sided")
    g, gci = hedges_g(a, b)
    ci = bootstrap_diff_ci(a, b)
    ci_med = bootstrap_diff_ci(a, b, stat=np.median)
    out.update(mean_a=float(a.mean()), mean_b=float(b.mean()), median_a=float(np.median(a)),
               median_b=float(np.median(b)), diff_mean=float(a.mean() - b.mean()), diff_mean_ci=list(ci),
               diff_median=float(np.median(a) - np.median(b)), diff_median_ci=list(ci_med), hedges_g=g,
               hedges_g_ci=list(gci), cliffs_delta=cliffs_delta(a, b), p_welch=float(welch.pvalue),
               p_mannwhitney=float(mw.pvalue), verdict=verdict(tuple(ci), len(a) + len(b), welch.pvalue),
               distribution_a=np.percentile(a, [5, 25, 50, 75, 95]).tolist(),
               distribution_b=np.percentile(b, [5, 25, 50, 75, 95]).tolist())
    out["explanation"] = (
        f"Average on '{label_a}' days: {a.mean():.1f} (n={len(a)}); on '{label_b}' days: {b.mean():.1f} (n={len(b)}). "
        f"Difference {a.mean() - b.mean():+.1f} (95% bootstrap CI {ci[0]:+.1f} to {ci[1]:+.1f}); "
        f"standardized effect g = {g:.2f}. "
        + ("The interval includes zero, so the result is inconclusive. " if ci[0] <= 0 <= ci[1] else "")
        + "Other differences between these days (weekday, workload, illness) may explain part of any gap.")
    return out


def benjamini_hochberg(pvals: list[float]) -> list[float]:
    p = np.array([1.0 if (v is None or math.isnan(v)) else v for v in pvals], dtype=float)
    n = len(p)
    if n == 0:
        return []
    order = np.argsort(p)
    ranked = p[order] * n / (np.arange(n) + 1)
    q = np.minimum.accumulate(ranked[::-1])[::-1]
    out = np.empty(n)
    out[order] = np.clip(q, 0, 1)
    return out.tolist()


def regression(y: pd.Series, X: pd.DataFrame, exposure: str) -> dict:
    """OLS with Newey–West (HAC) standard errors; reports the exposure coefficient adjusted for covariates."""
    import statsmodels.api as sm
    df = pd.concat([y.rename("__y"), X], axis=1).dropna()
    k = X.shape[1]
    if len(df) < max(20, 5 * (k + 1)):
        return {"verdict": "insufficient_data", "n": len(df),
                "explanation": f"Adjusted model needs at least {max(20, 5 * (k + 1))} complete days; have {len(df)}."}
    Xc = df[X.columns].loc[:, df[X.columns].std() > 0]
    if exposure not in Xc.columns:
        return {"verdict": "insufficient_data", "n": len(df), "explanation": "Exposure does not vary."}
    model = sm.OLS(df["__y"], sm.add_constant(Xc)).fit(cov_type="HAC", cov_kwds={"maxlags": 2})
    ci = model.conf_int().loc[exposure].tolist()
    return {"n": int(model.nobs), "coef": float(model.params[exposure]), "ci": ci,
            "p": float(model.pvalues[exposure]), "r2": float(model.rsquared),
            "covariates": [c for c in Xc.columns if c != exposure],
            "verdict": "inconclusive" if ci[0] <= 0 <= ci[1] else "association_detected",
            "coefficients": {c: float(v) for c, v in model.params.items()}}


def lagged(db: Database, x_feature: str, y_feature: str, start: date, end: date, lags=(0, 1, 2, 3),
           method: str = "spearman") -> list[dict]:
    f = load_features(db, [x_feature, y_feature], start, end + timedelta(days=max(lags)))
    out = []
    for lag in lags:
        y = f[y_feature].shift(-lag)
        r = correlate(f[x_feature].loc[:end], y.loc[:end], method)
        r["lag_days"] = lag
        out.append(r)
    return out


def rolling_correlation(x: pd.Series, y: pd.Series, window: int = 30) -> list[dict]:
    df = pd.concat([x.rename("x"), y.rename("y")], axis=1)
    rc = df["x"].rolling(window, min_periods=max(MIN_PAIRS, window // 2)).corr(df["y"])
    return [{"date": d.isoformat() if hasattr(d, "isoformat") else str(d), "r": None if pd.isna(v) else float(v)}
            for d, v in rc.items()]


def scan(db: Database, exposures: list[str], outcomes: list[str], start: date, end: date,
         lag: int | None = None) -> dict:
    """Exploratory all-pairs screen with Benjamini–Hochberg FDR control (lag None = outcome-specific default)."""
    feats = list(dict.fromkeys(exposures + outcomes))
    f = load_features(db, feats, start, end + timedelta(days=1))
    results = []
    for xe in exposures:
        for yo in outcomes:
            if xe == yo:
                continue
            lg = default_lag(yo, xe) if lag is None else lag
            r = correlate(f[xe].loc[:end], f[yo].shift(-lg).loc[:end], "spearman")
            results.append({"exposure": xe, "outcome": yo, "lag_days": lg, **{k: r.get(k) for k in (
                "r", "ci", "p", "n", "n_effective", "verdict", "strength")}})
    q = benjamini_hochberg([r["p"] for r in results])
    labels = habit_labels(db)
    for r, qv in zip(results, q):
        r["q"] = qv
        r["survives_fdr"] = bool(r["p"] is not None and qv < 0.10 and r["n"] >= SMALL_N)
        r["exposure_label"] = labels.get(r["exposure"]) or feature_info(r["exposure"])["label"]
        r["outcome_label"] = labels.get(r["outcome"]) or feature_info(r["outcome"])["label"]
    results.sort(key=lambda r: (not r["survives_fdr"], r["q"] if r["q"] is not None else 1))
    return {"tests": len(results), "results": results, "fdr_level": 0.10, "algorithm": VERSION,
            "warning": f"{len(results)} comparisons were tested. With many tests some 'findings' occur by chance; "
                       "only results surviving FDR control (q < 0.10, n ≥ 20) are highlighted, and even these are "
                       "associations to test with an experiment, not conclusions."}


def habit_labels(db: Database) -> dict[str, str]:
    return {f"j:{r['key']}": r["label"] for r in db.query("SELECT key, label FROM journal_habits")}


def apply_condition(series: pd.Series, condition: dict | None) -> pd.Series:
    """Dichotomize a habit series: {op: '>=', value: 1} / {op:'after', value:'15:00'} etc. Missing stays missing."""
    if not condition:
        return series.where(series.isna(), series > 0)
    op, val = condition.get("op", ">"), condition.get("value", 0)
    if isinstance(val, str) and ":" in val:
        h, m = val.split(":")
        val = int(h) * 60 + int(m)
        op = {"after": ">", "before": "<"}.get(op, op)
    val = float(val)
    ops = {">": series > val, ">=": series >= val, "<": series < val, "<=": series <= val, "==": series == val,
           "!=": series != val}
    if op not in ops:
        raise ValueError(f"unsupported operator {op}")
    return ops[op].where(series.notna())


OVERNIGHT_PREFIXES = ("sleep_", "rhr", "hrv_night", "spo2_night", "resp_rate_night", "skin_temp", "hr_sleep",
                      "recovery", "nap_min")


def is_overnight(feature: str) -> bool:
    return feature.startswith(OVERNIGHT_PREFIXES)


def default_lag(outcome: str, exposure: str | None = None) -> int:
    """Overnight outcomes are dated by the wake-up day, so a daytime exposure on day D relates to the outcome on
    D+1. When the exposure is itself an overnight measure (same night), the lag is 0."""
    if not is_overnight(outcome):
        return 0
    if exposure is not None and is_overnight(exposure) and not exposure.startswith("j:"):
        return 0
    return 1


def habit_analysis(db: Database, habit_feature: str, outcome: str, start: date, end: date, lag: int | None = None,
                   condition: dict | None = None, adjust: list[str] | None = None) -> dict:
    if lag is None:
        lag = default_lag(outcome, habit_feature)
    f = load_features(db, list(dict.fromkeys([habit_feature, outcome] + (adjust or []))), start,
                      end + timedelta(days=lag))
    x = f[habit_feature].loc[:end]
    y = f[outcome].shift(-lag).loc[:end]
    mask = apply_condition(x, condition)
    res = {"habit": habit_feature, "outcome": outcome, "lag_days": lag, "condition": condition,
           "period": [start.isoformat(), end.isoformat()], "days_with_habit_logged": int(x.notna().sum()),
           "days_with_outcome": int(y.notna().sum())}
    res["group_comparison"] = compare_groups(y, mask, "condition met", "condition not met")
    res["correlation"] = correlate(x, y, "spearman")
    if adjust:
        X = pd.DataFrame({habit_feature: mask.astype(float)})
        for c in adjust:
            X[c] = f[c].loc[:end]
        idx = pd.to_datetime(pd.Series(X.index))
        X["weekend"] = (idx.dt.weekday >= 5).astype(float).to_numpy()
        res["adjusted"] = regression(y, X, habit_feature)
    res["caution"] = ("Observational comparison of your own days. Habits often travel together (e.g. late coffee on "
                      "busy days), so differences may reflect other factors. Use an experiment to test it.")
    return res
