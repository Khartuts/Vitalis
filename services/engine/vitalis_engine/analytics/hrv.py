"""Time-domain HRV from genuine beat-to-beat (inter-beat) intervals only.

This module is never applied to averaged heart-rate samples. Input must be a sequence of RR/IBI values in
milliseconds from a source that reports individual beats (e.g. a chest strap export).
"""

from __future__ import annotations

import numpy as np

MIN_IBI_MS, MAX_IBI_MS = 300.0, 2000.0
MAX_SUCCESSIVE_CHANGE = 0.20  # reject beats differing >20 % from the previous accepted beat (artefact filter)


def clean_ibi(ibi_ms: list[float] | np.ndarray) -> tuple[np.ndarray, dict]:
    x = np.asarray(ibi_ms, dtype=float)
    ok = (x >= MIN_IBI_MS) & (x <= MAX_IBI_MS)
    kept = []
    rejected_change = 0
    for v in x[ok]:
        if kept and abs(v - kept[-1]) / kept[-1] > MAX_SUCCESSIVE_CHANGE:
            rejected_change += 1
            continue
        kept.append(v)
    arr = np.array(kept)
    return arr, {"input_beats": int(len(x)), "out_of_range": int((~ok).sum()), "rejected_successive": rejected_change,
                 "kept": int(len(arr)), "artefact_fraction": float(1 - len(arr) / max(1, len(x)))}


def time_domain(ibi_ms: list[float] | np.ndarray, min_beats: int = 60, max_artefact_fraction: float = 0.2) -> dict:
    arr, q = clean_ibi(ibi_ms)
    if len(arr) < min_beats or q["artefact_fraction"] > max_artefact_fraction:
        return {"status": "insufficient_quality", "quality": q, "rmssd_ms": None, "sdnn_ms": None}
    diffs = np.diff(arr)
    return {"status": "ok", "quality": q, "rmssd_ms": float(np.sqrt(np.mean(diffs ** 2))),
            "sdnn_ms": float(np.std(arr, ddof=1)), "mean_hr_bpm": float(60000.0 / arr.mean()),
            "pnn50": float(np.mean(np.abs(diffs) > 50.0) * 100.0)}
