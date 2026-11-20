# Algorithms (versioned)

Every stored result records the algorithm version that produced it (`daily_features.algo_version`,
`scores.algo_version`, `analysis_versions`). Versions: `features.v1`, `baseline.robust.v1`, `recovery.v1`,
`energy.v1-exploratory`, `load.trimp-edwards.v1`, `arousal.v1`, `sri.v1`, `sleepdebt.ewma.v1`, `insights.v1`,
`stats.v1`, `nof1.v1`. These are **original VITALIS methods** built on published physiological and statistical
concepts; they do not reproduce WHOOP Recovery, Garmin Body Battery, Samsung Energy Score or any vendor score.

## 0. Ingestion, time and quality
* Instants are stored in UTC with the original offset. A record's local date uses its own offset (so travel days stay
  correct); if no offset is known, the display time zone (default Europe/Zagreb, DST-aware via `zoneinfo`) is used
  and the record is flagged `TZ_INFERRED`. Sleep is dated by the **local date of the wake time**; the wake time uses
  the session's end offset or, if absent, a DST-aware zone conversion (so nights spanning the October/March changes
  are correct).
* Stable record IDs: UUIDv5(provider | provider record ID or content hash | metric | start | sub-key). Re-imports
  are idempotent; changed values update in place; provider deletions soft-delete.
* Flags: `OUT_OF_RANGE` (physiological plausibility ranges per metric), `FUTURE_TIMESTAMP`, `TZ_INFERRED`,
  `OUTLIER` (heart rate: |x − rolling median(9)| > 40 bpm outside exercise ± 10 min). Flagged values are kept but
  excluded from analytics.
* Cross-source duplicates: same metric, |Δt| ≤ 60 s and |Δvalue| ≤ tolerance (e.g. HR 3 bpm) from different providers
  → lower-priority record `superseded_by` the winner; sleep/exercise sessions overlapping ≥ 50 % of the shorter one
  likewise. Summed metrics (steps, energy, distance) use **one provider per day** (highest priority with data) —
  never a cross-source sum. Within Health Connect, totals come from HC's de-duplicating aggregate API.
* Units: only physically compatible conversions (g→kg, mm→m, mg/dL→mmol/L, °F→°C …); incompatible units raise.

## 1. Sleep features (`features.v1`)
* Episodes: sessions merged when the gap ≤ 60 min; the **main sleep** is the longest episode ≥ 180 min ending on
  that date; others are naps. Samsung export stage rows are attached to their parent session by time containment.
* TST = Σ(light, deep, REM, generic "asleep") stage minutes; without stages TST = episode duration and every
  stage-dependent metric is left **missing** (never inferred). TIB = episode duration − out-of-bed time;
  efficiency = TST/TIB.
* Latency: only when the episode begins with an awake/in-bed stage (otherwise reported "n/a"). WASO = awake minutes
  between first and last sleep stage. Awakenings = awake bouts ≥ 2 min (bouts < 60 s apart merged).
* Stage % of TST only when generic "asleep" is < 5 % of TST. Bed/wake/midpoint = wall-clock minutes from local
  midnight of the wake date (negative = before midnight).
* Overnight vitals within the main sleep window: HR mean/min; **calculated resting HR** = lowest 30-minute rolling
  mean of HR (≥ 3 samples, ≥ 6 samples/night); HRV = mean of *source-reported* RMSSD values (± 30 min) or the
  source's daily RMSSD summary; SpO2 mean/min (≥ 3 samples); respiratory rate (−1 h/+2 h window); skin-temperature
  deviation (relative to the source baseline — never treated as absolute).
* `rhr` = calculated overnight value when available, else source-reported (method recorded in `detail`).

## 2. Sleep regularity, debt, planner, consistency
* **SRI (sri.v1)** — Phillips et al. 2017 style: minute-by-minute sleep/wake arrays for each local day (asleep
  stages, or whole episode without stages); SRI = −100 + 200·P(same state at t and t+24 h) over consecutive-day
  pairs in a 14-day window, requiring ≥ 5 valid pairs. DST days are resampled to 1 440 clock minutes.
* **Sleep debt (sleepdebt.ewma.v1)** — debtₜ = max(0, 0.85·debtₜ₋₁ + (need − TSTₜ)); nights without data only decay.
  Need = user setting (default 450 min). An illustration of accumulated shortfall, not a physiological quantity.
* **Planner** — TIB = need ÷ median efficiency (last 28 nights, ≥ 5 nights, else 90 %, clipped 75–98 %);
  bedtime = wake − TIB; window ± 15 min; if > 45 min from the typical bedtime, a gradual plan shifts 15 min every
  2 days. Adherence: bedtime within window ± 15 min **and** wake within ± 30 min.
* Social jet lag = |median midpoint (Sat/Sun wake dates) − median midpoint (weekdays)|.

## 3. Activity, load, arousal
* HR sample weights: gap to next sample capped at 10 min; before a long gap, the gap to the previous sample (so a
  dense workout series does not leak into unmeasured time).
* **Zones** (Edwards): 50–60/60–70/70–80/80–90/≥ 90 % HRmax; HRmax = user value or Tanaka 208 − 0.7·age; otherwise
  zones are unavailable. **TRIMP** = Σ zone minutes × zone index (1–5).
* **Load (load.trimp-edwards.v1)**: acute = EWMA(span 7) and chronic = EWMA(span 28) of daily TRIMP; days without HR
  count as 0; requires ≥ 14 observed days in 28; ratio reported as exploratory.
* Inactive waking hours: hours with HR present (watch worn), outside sleep, with < 250 steps.
* **Arousal (arousal.v1)**: low-movement awake samples = outside sleep, outside exercise ± 10 min, in clock hours with
  < 300 steps. Reference = median low-movement HR over the previous 14 days (≥ 3 days; else same day).
  *Arousal minutes* = time-weighted minutes ≥ 12 bpm above the reference. Labeled as a physiological signal, not
  psychological stress. Intraday timelines use 15-minute bins with states sleep / exercise / active / calm /
  elevated.

## 4. Baselines (`baseline.robust.v1`)
For day D: values of the previous W days (default 60; strictly before D). n < 14 → *insufficient*, 14–27 →
*provisional*, ≥ 28 → *stable* (all configurable). Median and robust SD = 1.4826·MAD, floored per feature (e.g.
RHR 1 bpm, HRV 3 ms, TST 20 min). z = (x − median)/robust SD, clipped to ± 4.

## 5. Morning Recovery Estimate (`recovery.v1`)
Components (score s, weight w): HRV +z (0.35); resting HR −z (0.25); sleep (TST − need)/60 clipped [−2.5, 1]
(0.25); bedtime regularity −max(0, |dev| − 30)/45 clipped [−2, 0] (0.075); previous-day load −0.5·max(0, z_TRIMP)
clipped [−1.5, 0] (0.075). Physiological z-scores clipped ± 3. Requires **(HRV or RHR with a non-insufficient
baseline) and sleep duration**, otherwise *Insufficient data* with reasons. Composite z = Σ wᵢsᵢ / Σ wᵢ over available
components; index = clip(50 + 20 z, 0, 100); completeness = Σ available weights. Categories: z ≥ 1 well above,
≥ 0.35 above, > −0.35 typical, > −1 below, else well below. Every estimate stores inputs used/missing, contributions,
baseline status and limitations. **Not** a medical percentage.

## 6. Daily Energy Estimate (`energy.v1-exploratory`)
Hand-set, unvalidated parameters, shown in the UI: start at wake = clip(50 + 20·recovery z, 15, 95); per 15-min bin:
base drain 1.8/h; activity drain 9/h × HRR^1.5 (HRR = (HR − RHR)/(HRmax − RHR)), or steps-based fallback; elevated
low-movement drain 1.6/h; calm-rest recharge 1/h; sleep/nap recharge 7/h × 0.85; clamped 0–100. Requires a recovery
estimate and a main sleep; otherwise *Insufficient data*.

## 7. Statistics (`stats.v1`)
* Pearson (with a Shapiro normality note) or Spearman; CI via Fisher z (Bonett–Wright SE for Spearman) using an
  **effective N** = n(1 − r₁ₓr₁ᵧ)/(1 + r₁ₓr₁ᵧ) to account for day-to-day autocorrelation; minimum 10 pairs.
* Verdicts: *insufficient_data* (n < 10), *inconclusive* (CI includes 0), *suggestive_small_sample* (n < 20),
  *association_detected*. Texts always state that association ≠ causation.
* Default lag: an overnight outcome (sleep, RHR, HRV, recovery) relates to a **daytime** exposure on the previous
  day (lag 1); overnight-to-overnight comparisons use lag 0.
* Group comparison: Welch t, Mann–Whitney U, Hedges g with CI, Cliff's δ, bootstrap (4 000, seeded) CIs for the
  difference in means and medians; ≥ 5 days per group.
* Adjusted analysis: OLS with Newey–West HAC SE (maxlags 2), exposure + weekend + optional covariates; requires
  ≥ max(20, 5·(k+1)) complete days.
* Screens ("Discover", insights): all habit × outcome pairs with Benjamini–Hochberg FDR; highlighted only if
  q < 0.10 and n ≥ 20.
* HRV from beat intervals (`analytics/hrv.py`): only for genuine IBI input; range 300–2000 ms, reject beats changing
  > 20 % from the previous accepted beat, require ≥ 60 beats and ≤ 20 % artefacts; RMSSD, SDNN, pNN50.

## 8. N-of-1 experiments (`nof1.v1`)
Baseline days then either A-B or **randomized blocks** (block length default 2 days; balanced pairs in random order;
seeded). Adherence = share of non-baseline days whose logged habit matches the assigned arm. Analysis
(intention-to-treat): difference in means with bootstrap CI, Hedges g, permutation test (10 000; block labels permuted
for randomized designs); ≥ 7 days per arm; "Inconclusive" when the CI includes 0.

## 9. Insights (`insights.v1`)
Deterministic rules: RHR z ≥ 1.5 on ≥ 2 of the last 3 nights; 7-night mean TST ≥ 30 min below/above baseline
(≥ 5 nights); SRI change ≥ 10 vs 14 days earlier; 7-night HRV mean |z| ≥ 1 (≥ 4 nights); steps ±25 % vs prior 28
days; recovery z change ≥ 0.6 week-over-week; 90-day RHR trend (Theil–Sen, Kendall p < 0.01, ≥ 1 bpm/30 d);
3-day data gaps; FDR-surviving habit associations with |ρ| ≥ 0.3. Each insight has numbers, baseline, completeness,
confidence, action and evidence links; deduplicated by key, one insight per topic per period.
