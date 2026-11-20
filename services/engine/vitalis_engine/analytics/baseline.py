"""Robust personal baselines (baseline.robust.v1): rolling median + scaled MAD over prior days only."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from ..db.connection import Database

# Minimum meaningful dispersion per feature, so that unusually stable periods do not create huge z-scores.
SD_FLOOR = {"rhr": 1.0, "rhr_sleep_est": 1.0, "hr_sleep_mean": 1.0, "hrv_night_rmssd": 3.0, "sleep_tst_min": 20.0,
            "sleep_efficiency": 2.0, "sleep_bedtime_min": 15.0, "sleep_wake_min": 15.0, "sleep_midpoint_min": 15.0,
            "trimp": 10.0, "steps": 800.0, "resp_rate_night": 0.3, "spo2_night_mean": 0.5,
            "skin_temp_delta_night": 0.1, "skin_temp_night": 0.1, "arousal_min": 5.0, "active_kcal": 50.0,
            "sleep_waso_min": 5.0, "sleep_deep_min": 8.0, "sleep_rem_min": 8.0}


@dataclass
class Baseline:
    feature: str
    on: str
    n: int
    window_days: int
    status: str            # insufficient | provisional | stable
    median: float | None
    robust_sd: float | None
    p25: float | None
    p75: float | None

    def z(self, value: float | None) -> float | None:
        if value is None or self.median is None or self.robust_sd is None or self.status == "insufficient":
            return None
        return float(np.clip((value - self.median) / self.robust_sd, -4, 4))

    def as_dict(self) -> dict:
        return asdict(self)


def settings(db: Database) -> tuple[int, int, int]:
    return (int(db.get_pref("baseline_window_days", 60)), int(db.get_pref("baseline_min_provisional", 14)),
            int(db.get_pref("baseline_min_stable", 28)))


def compute(series: pd.Series, feature: str, on: date, window: int = 60, min_prov: int = 14,
            min_stable: int = 28) -> Baseline:
    """series: values indexed by python date. Uses days strictly before `on`."""
    lo = on - timedelta(days=window)
    s = series[(series.index >= lo) & (series.index < on)].dropna()
    n = int(len(s))
    status = "insufficient" if n < min_prov else ("provisional" if n < min_stable else "stable")
    if n == 0:
        return Baseline(feature, on.isoformat(), 0, window, status, None, None, None, None)
    v = s.to_numpy(dtype=float)
    med = float(np.median(v))
    mad = float(np.median(np.abs(v - med))) * 1.4826
    rsd = max(mad, SD_FLOOR.get(feature, 1e-6))
    return Baseline(feature, on.isoformat(), n, window, status, med, rsd, float(np.percentile(v, 25)),
                    float(np.percentile(v, 75)))


def baseline_for(db: Database, feature: str, on: date, frame: pd.DataFrame | None = None) -> Baseline:
    from .data import load_features
    window, mp, ms = settings(db)
    if frame is None or feature not in frame:
        frame = load_features(db, [feature], on - timedelta(days=window), on)
    return compute(frame[feature], feature, on, window, mp, ms)
