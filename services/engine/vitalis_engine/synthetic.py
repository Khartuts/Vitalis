"""Synthetic demo data — clearly labeled, never mixed into a real profile.

Produces records in the companion's Health Connect wire format (vitalis.sync.v1) so the demo exercises the
real import path. Provider = "synthetic", provenance = "synthetic", origin = "vitalis.synthetic.demo".
Built-in, documented effects (so the Correlation Lab has something to find): late caffeine delays sleep and
reduces deep sleep; alcohol raises next-night resting HR and lowers HRV; walking raises steps and slightly
improves efficiency; high perceived stress raises daytime HR at low movement; an illness episode around day 55;
a gradual resting-HR improvement; nights without the watch and an HR gap as missing-data examples.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np

from .db.connection import Database
from .ingest import health_connect
from .ingest.pipeline import Importer
from .journal import ensure_builtins, set_day
from .model.canonical import SourceInfo
from .model.metrics import Provenance

ORIGIN = "vitalis.synthetic.demo"
DEVICE = {"type": "WATCH", "manufacturer": "Synthetic", "model": "Demo Watch (not a real device)"}


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _off(dt: datetime) -> str:
    o = dt.utcoffset() or timedelta(0)
    m = int(o.total_seconds() // 60)
    return f"{'+' if m >= 0 else '-'}{abs(m) // 60:02d}:{abs(m) % 60:02d}"


def generate(days: int = 90, end: date | None = None, seed: int = 20261120, tz: str = "Europe/Zagreb") -> dict:
    rng = np.random.default_rng(seed)
    Z = ZoneInfo(tz)
    end = end or date.today()
    start = end - timedelta(days=days - 1)
    recs: list[dict] = []
    journal: dict[str, dict] = {}
    rid = [0]

    def nid(prefix):
        rid[0] += 1
        return f"syn-{prefix}-{rid[0]:07d}"

    def base(t, d0=None):
        return {"origin": ORIGIN, "device": DEVICE, "recordingMethod": "automatically_recorded",
                "lastModified": _iso(datetime.now(timezone.utc))}

    # 1) Each day's true behaviour (logged on ~88 % of days, like a real journal).
    truth: dict[date, dict] = {}
    for i in range(days + 2):
        d = start - timedelta(days=1) + timedelta(days=i)
        workday = d.weekday() < 5
        coffee_cups = int(rng.poisson(2.2))
        late_coffee = bool(rng.random() < 0.3) and coffee_cups > 0
        caffeine_time = (f"{(rng.integers(15, 18) if late_coffee else rng.integers(8, 13)):02d}:"
                         f"{rng.choice([0, 15, 30, 45]):02d}") if coffee_cups > 0 else None
        stress = int(np.clip(round(rng.normal(6.3 if workday else 3.8, 1.6)), 1, 10))
        tv = {"coffee_cups": coffee_cups, "caffeine_last_time": caffeine_time, "caffeine_after_lunch": late_coffee,
              "alcohol_units": int(rng.poisson(1.6 if d.weekday() in (4, 5) else 0.3)),
              "walk": bool(rng.random() < 0.6), "stress": stress,
              "mood": int(np.clip(round(8 - 0.4 * stress + rng.normal(0, 1)), 1, 10)),
              "energy": int(np.clip(round(7 - 0.3 * stress + rng.normal(0, 1.2)), 1, 10)),
              "late_snack": bool(rng.random() < 0.25), "screen_before_bed": bool(rng.random() < 0.55),
              "water_l": float(np.clip(round(rng.normal(1.8, 0.4) * 4) / 4, 0.5, 4)),
              "workload": "heavy" if stress >= 7 else ("day off" if not workday else "normal"),
              "exercise": d.weekday() in (0, 2, 5)}
        truth[d] = tv
        if i >= 1 and d <= end and rng.random() < 0.88:
            journal[d.isoformat()] = tv

    # 2) Physiology driven by that behaviour.
    for i in range(days + 1):
        d = start - timedelta(days=1) + timedelta(days=i)  # include the evening before the first day
        weekend_night = d.weekday() in (4, 5)  # Fri/Sat evening -> weekend morning
        ill = 54 <= i <= 58
        trend = -2.0 * i / max(days, 1)
        tv = truth[d]
        late_coffee, alcohol, stress = tv["caffeine_after_lunch"], tv["alcohol_units"], tv["stress"]
        walk, screen = tv["walk"], tv["screen_before_bed"]
        # ---- the night d -> d+1 ----
        nd = d + timedelta(days=1)
        if nd > end:
            break
        no_watch = i in (20, 21, 64)
        bed_mean = 24 * 60 + (25 if weekend_night else -45)
        bed = bed_mean + rng.normal(0, 35 if weekend_night else 22) + (25 if late_coffee else 0) + (10 if screen else 0)
        wake = (8 * 60 + 35 if nd.weekday() >= 5 else 6 * 60 + 50) + rng.normal(0, 30 if nd.weekday() >= 5 else 9)
        if ill:
            wake += 50
        bed_dt = datetime(d.year, d.month, d.day, tzinfo=Z) + timedelta(minutes=bed)
        wake_dt = datetime(nd.year, nd.month, nd.day, tzinfo=Z) + timedelta(minutes=wake)
        # Effects of day d's habits appear in the night d -> d+1 (dated by wake day d+1, i.e. lag 1).
        rhr_night = 55.5 + trend + 1.6 * alcohol + 0.35 * (stress - 5) + (6.5 if ill else 0) + rng.normal(0, 1.1)
        hrv_night = 49 - 2.0 * trend - 4.0 * alcohol - 0.9 * (stress - 5) - (14 if ill else 0) + rng.normal(0, 4)
        if not no_watch:
            stages = []
            t = bed_dt
            latency = max(3, rng.normal(14 + (8 if late_coffee else 0), 5))
            stages.append({"startTime": _iso(t), "endTime": _iso(t + timedelta(minutes=latency)), "stage": "awake"})
            t += timedelta(minutes=latency)
            cyc = 0
            deep_scale = 0.75 if late_coffee else 1.0
            eff_bonus = 1.0 if walk else 0.0
            while t < wake_dt - timedelta(minutes=10):
                cyc += 1
                frac = cyc / 5.0
                seq = [("light", rng.normal(25, 6)), ("deep", max(4, rng.normal(32 - 6 * cyc, 6)) * deep_scale),
                       ("light", rng.normal(14, 4)), ("rem", max(5, rng.normal(10 + 6 * cyc, 4)))]
                for stg, dur in seq:
                    dur = max(3.0, dur)
                    e = min(t + timedelta(minutes=dur), wake_dt)
                    if e <= t:
                        break
                    stages.append({"startTime": _iso(t), "endTime": _iso(e), "stage": stg})
                    t = e
                if t < wake_dt and rng.random() < (0.55 - 0.08 * eff_bonus) * (1.3 if alcohol else 1.0):
                    aw = max(2, rng.normal(6, 3))
                    e = min(t + timedelta(minutes=aw), wake_dt)
                    stages.append({"startTime": _iso(t), "endTime": _iso(e), "stage": "awake"})
                    t = e
            recs.append({"type": "SleepSessionRecord", "id": nid("sleep"), **base(bed_dt),
                         "startTime": _iso(bed_dt), "startZoneOffset": _off(bed_dt), "endTime": _iso(wake_dt),
                         "endZoneOffset": _off(wake_dt), "title": None, "stages": stages})
            # overnight HR every 10 min, SpO2 every 30 min, respiratory rate + skin temp + HRV once
            t = bed_dt
            samples = []
            while t < wake_dt:
                prog = (t - bed_dt).total_seconds() / max(1, (wake_dt - bed_dt).total_seconds())
                hr_v = rhr_night + 3.5 * math.cos(prog * math.pi) ** 2 + rng.normal(0, 1.5)
                samples.append({"time": _iso(t), "bpm": int(round(hr_v))})
                t += timedelta(minutes=10)
            recs.append({"type": "HeartRateRecord", "id": nid("hr"), **base(bed_dt), "startTime": _iso(bed_dt),
                         "startZoneOffset": _off(bed_dt), "endTime": _iso(wake_dt), "endZoneOffset": _off(wake_dt),
                         "samples": samples})
            t = bed_dt + timedelta(minutes=20)
            while t < wake_dt:
                recs.append({"type": "OxygenSaturationRecord", "id": nid("spo2"), **base(t), "time": _iso(t),
                             "zoneOffset": _off(t), "percent": float(np.clip(round(rng.normal(95.8 - (1.2 if ill else 0),
                                                                                                0.9), 0), 88, 100))})
                t += timedelta(minutes=30)
            mid = bed_dt + (wake_dt - bed_dt) / 2
            recs.append({"type": "RespiratoryRateRecord", "id": nid("rr"), **base(mid), "time": _iso(mid),
                         "zoneOffset": _off(mid), "rate": round(float(rng.normal(14.2 + (1.5 if ill else 0), 0.4)), 1)})
            recs.append({"type": "HeartRateVariabilityRmssdRecord", "id": nid("hrv"), **base(mid), "time": _iso(mid),
                         "zoneOffset": _off(mid), "rmssdMs": round(float(np.clip(hrv_night, 12, 140)), 1)})
            recs.append({"type": "SkinTemperatureRecord", "id": nid("skt"), **base(bed_dt),
                         "startTime": _iso(bed_dt), "startZoneOffset": _off(bed_dt), "endTime": _iso(wake_dt),
                         "endZoneOffset": _off(wake_dt), "baselineC": None, "location": "wrist",
                         "deltas": [{"time": _iso(mid), "deltaC": round(float(rng.normal(0.45 if ill else 0.0, 0.12)), 2)}]})
        # ---- daytime nd: HR every 10 min, steps hourly, exercise ----
        if no_watch:
            continue
        day_start = wake_dt
        next_bed = datetime(nd.year, nd.month, nd.day, tzinfo=Z) + timedelta(minutes=23 * 60)
        ex_day = nd.weekday() in (0, 2, 5) and not ill
        ex_start = datetime(nd.year, nd.month, nd.day, 18, 0, tzinfo=Z) + timedelta(minutes=int(rng.integers(-60, 60)))
        ex_len = int(rng.integers(40, 65))
        ex_end = ex_start + timedelta(minutes=ex_len)
        j = truth.get(nd, {})
        stress_today = j.get("stress", 5)
        walk_today = j.get("walk", False)
        gap = (nd - start).days == 33  # a 6-hour sensor gap example
        hourly_steps = {}
        for h in range(24):
            hs = datetime(nd.year, nd.month, nd.day, h, tzinfo=Z)
            if hs < wake_dt or hs >= next_bed:
                continue
            base_steps = rng.gamma(2.0, 180 if (9 <= h <= 17 and nd.weekday() < 5) else 260)
            if walk_today and h in (12, 19):
                base_steps += rng.normal(2600, 400)
            if ex_day and ex_start.hour == h:
                base_steps += rng.normal(3000, 600)
            hourly_steps[h] = max(0, int(base_steps))
            recs.append({"type": "StepsAggregateHourly", "origin": ORIGIN, "startTime": _iso(hs),
                         "startZoneOffset": _off(hs), "endTime": _iso(hs + timedelta(hours=1)),
                         "value": hourly_steps[h], "dataOrigins": [ORIGIN]})
        samples, t = [], day_start
        while t < next_bed:
            if gap and 10 <= t.hour < 16:
                t += timedelta(minutes=10)
                continue
            in_ex = ex_day and ex_start <= t < ex_end
            step = timedelta(minutes=1 if in_ex else 10)
            if in_ex:
                prog = (t - ex_start).total_seconds() / (ex_len * 60)
                hr_v = 118 + 38 * math.sin(prog * math.pi) + rng.normal(0, 4)
            else:
                act = hourly_steps.get(t.hour, 0) / 1000.0
                stress_bump = 0.0
                if 9 <= t.hour <= 17 and act < 0.3:
                    stress_bump = max(0, stress_today - 5) * 1.5
                    if stress_today >= 7 and rng.random() < 0.25:  # episodic elevations on high-stress days
                        stress_bump += rng.normal(16, 4)
                spike = 0
                if rng.random() < 0.004:  # rare optical artefact, should be flagged as outlier
                    spike = rng.choice([55, -35])
                hr_v = rhr_night + 13 + 9 * min(act, 2.5) + stress_bump + (5 if ill else 0) + rng.normal(0, 3) + spike
            samples.append({"time": _iso(t), "bpm": int(round(max(35, hr_v)))})
            t += step
        recs.append({"type": "HeartRateRecord", "id": nid("hr"), **base(day_start), "startTime": _iso(day_start),
                     "startZoneOffset": _off(day_start), "endTime": _iso(next_bed), "endZoneOffset": _off(next_bed),
                     "samples": samples})
        if ex_day:
            recs.append({"type": "ExerciseSessionRecord", "id": nid("ex"), **base(ex_start),
                         "startTime": _iso(ex_start), "startZoneOffset": _off(ex_start), "endTime": _iso(ex_end),
                         "endZoneOffset": _off(ex_end), "exerciseType": "running" if nd.weekday() != 5 else "biking",
                         "title": "Synthetic workout"})
        recs.append({"type": "ActiveCaloriesBurnedRecord", "id": nid("kcal"), **base(day_start),
                     "startTime": _iso(day_start), "startZoneOffset": _off(day_start), "endTime": _iso(next_bed),
                     "endZoneOffset": _off(next_bed),
                     "kcal": round(sum(hourly_steps.values()) * 0.04 + (380 if ex_day else 0), 1)})
        if nd.weekday() == 0:
            t = datetime(nd.year, nd.month, nd.day, 7, 15, tzinfo=Z)
            recs.append({"type": "WeightRecord", "id": nid("w"), **base(t), "recordingMethod": "manual_entry",
                         "time": _iso(t), "zoneOffset": _off(t), "kg": round(float(78.4 - 0.01 * i + rng.normal(0, 0.3)), 1)})
    return {"records": recs, "journal": journal, "start": start.isoformat(), "end": end.isoformat(), "seed": seed}


def _synthetic_adapter(rec: dict):
    out = health_connect.normalize(rec)
    out.source = SourceInfo("synthetic", ORIGIN, "WATCH", "Synthetic", "Demo Watch (not a real device)")
    for m in out.measurements:
        m.provenance = Provenance.SYNTHETIC
    for s in out.sleeps:
        s.provenance = Provenance.SYNTHETIC
    for e in out.exercises:
        e.provenance = Provenance.SYNTHETIC
    return out


def load_into(db: Database, days: int = 90, end: date | None = None, seed: int = 20261120) -> dict:
    if not db.get_pref("is_demo", False):
        raise PermissionError("synthetic data can only be loaded into a demo profile")
    data = generate(days, end, seed, db.get_pref("display_timezone", "Europe/Zagreb"))
    s = Importer(db).run(provider="synthetic", import_method="synthetic", records=data["records"],
                         adapter=_synthetic_adapter, detail={"seed": seed, "days": days})
    ensure_builtins(db)
    for d, vals in data["journal"].items():
        set_day(db, date.fromisoformat(d), {k: v for k, v in vals.items() if v is not None})
    db.execute("UPDATE journal_entries SET provenance='synthetic'")
    if not db.get_pref("birth_year"):
        db.set_pref("birth_year", 1988)
    return {"import": s.as_dict(), "journal_days": len(data["journal"]), "range": [data["start"], data["end"]]}
