import os
from datetime import date

import pytest

from vitalis_engine.db.connection import Database

END = date(2026, 11, 19)


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db", os.urandom(32).hex())
    yield d
    d.close()


@pytest.fixture(scope="session")
def demo_db(tmp_path_factory):
    """90 days of synthetic data, fully recomputed (shared, read-only use)."""
    from vitalis_engine import synthetic
    from vitalis_engine.analytics.recompute import recompute
    p = tmp_path_factory.mktemp("demo") / "demo.db"
    d = Database(p, os.urandom(32).hex())
    d.set_pref("is_demo", True)
    synthetic.load_into(d, 90, END)
    recompute(d, today=END)
    yield d
    d.close()


def hc(rtype, **kw):
    base = {"type": rtype, "id": kw.pop("id", f"id-{rtype}-{kw.get('time', kw.get('startTime', ''))}"),
            "origin": kw.pop("origin", "com.sec.android.app.shealth"),
            "device": kw.pop("device", {"type": "WATCH", "manufacturer": "Samsung", "model": "SM-R960"}),
            "recordingMethod": kw.pop("recordingMethod", "automatically_recorded")}
    base.update(kw)
    return base
