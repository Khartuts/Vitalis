"""VITALIS analytics engine.

Local-only, deterministic health analytics over an encrypted SQLCipher database.
"""

__version__ = "0.3.0"

# Algorithm versions are recorded with every stored result so that results can be
# traced to the exact code that produced them (see ALGORITHMS.md).
ALGORITHM_VERSIONS = {
    "features": "features.v1",
    "baseline": "baseline.robust.v1",
    "recovery": "recovery.v1",
    "energy": "energy.v1-exploratory",
    "load": "load.trimp-edwards.v1",
    "arousal": "arousal.v1",
    "sleep_regularity": "sri.v1",
    "sleep_debt": "sleepdebt.ewma.v1",
    "insights": "insights.v1",
    "thermal": "thermal.v1-exploratory",
    "stats": "stats.v1",
    "experiments": "nof1.v1",
}
