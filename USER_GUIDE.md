# User Guide

## First run
Create a **private profile** (stored only on this PC, encrypted) or a **demo profile** to explore with synthetic data
(a yellow banner is always shown; demo data never mixes with real data). Switch profiles in Settings → Profile.

## Everyday flow
1. Wear the watch, let Samsung Health sync, open the companion and tap **Sync now** (or enable hourly sync).
2. Morning: open **Overview** — recovery estimate, last night's sleep, trends vs your baseline, data quality, the most
   important change and up to three actionable insights. Click any card for details.
3. Evening: **Habit Journal → Daily check-in** (Ctrl+J). Logging caffeine, alcohol, walks, stress and mood is what
   makes the Correlation Lab useful.

## Explain buttons
Every card on the analysis screens has an **Explain** button. It opens a built-in explanation that works offline:
what the card shows, how it is calculated, how to read it and its limitations — plus **Your data**, which says how
many days of each input you have and whether your personal baseline is still building (estimates such as recovery
need about 14 nights). **Ask the analyst about this** opens the AI analyst with a question about the card; nothing is
sent until you have seen and approved the exact request.

## Screens
| Screen | What you get |
|---|---|
| Overview | Today vs your usual (robust 60-day baseline), 1/7/30/90-day comparisons, energy curve, insights. *Customize* reorders or hides cards. |
| Sleep Lab | Nightly sleep, efficiency, latency (when measurable), WASO, awakenings, stages and hypnogram, bedtime heatmap, SRI, weekday vs weekend, distribution, sleep debt (with assumptions), overnight vitals, **Sleep Planner** with adherence. |
| Recovery | Morning Recovery Estimate and *why* it changed (driver chart, inputs used/missing, baseline status), exploratory daily energy timeline, rest vs activity. |
| Stress & Nervous System | Minutes of elevated HR at low movement, intraday state timeline, workday vs weekend, morning vs afternoon, your stress ratings vs physiology, a quick stress journal. Not a psychological stress test. |
| Cardiovascular | Resting HR, HRV (only if a source provides it), day/night HR, HR distribution, SpO2, respiratory rate, zones, blood pressure, sleep↔RHR/HRV correlations, authorized heart records shown verbatim. |
| Activity & Fitness | Steps (de-duplicated), hourly heatmap, zones, TRIMP load (acute/chronic), weekly distribution, activity vs sleep, exercise history. |
| Health Trends | Any metric over time with smoothing, saved views, period comparison with confidence intervals, monthly view. |
| Habit Journal | Built-in and custom habits (yes/no, number, time, category, 1–10 scale, text), notes, history grid, private flag. |
| Correlation Lab | *Ask a question* templates, two-variable explorer (scatter, lags, rolling correlation), FDR-controlled discovery, **N-of-1 experiments** with randomized schedules. |
| AI Health Analyst | Chat with the analyst; saved conversations; every cloud request previewed; evidence chips open the exact data. |
| Reports | 7/30/90/365-day or custom reports (PDF, CSV, JSON, all data) and Morning/Weekly/Monthly/Quarterly briefs. |
| Data Sources & Settings | Profile & preferences, phone pairing, imports, Google Health wizard, AI providers, source priority, data quality, privacy (disclosure log, backup/restore, export, delete). |

## Reading the numbers
* **Baseline**: median of your previous 60 days (needs 14 valid days to be *provisional*, 28 to be *stable*).
* **Recovery index**: 50 = your usual; ±20 ≈ one robust standard deviation of your own history. It is relative, not a
  percentage and not medical.
* **Provenance badges**: *Measured* (sensor), *Imported* (vendor-computed, e.g. Samsung sleep score), *Calculated*
  (VITALIS deterministic), *Estimated* (VITALIS model), *You entered*, *Synthetic*.
* **Missing data** is shown as missing. VITALIS never fills gaps.
* **Correlation results** say *inconclusive* whenever the confidence interval includes "no effect". An association is
  not proof of cause — use an experiment.

## The AI analyst
Choose a provider at the top of the panel. Ask questions such as "Why was my recovery worse this morning?" or "Is
caffeine after 2 PM associated with worse sleep?". For cloud providers a dialog shows the exact data before each
request; *Cancel* sends nothing. Click a chart's **Ask AI** button to ask about that chart. The analyst cannot
diagnose, change medication or see private habits.

## Keyboard shortcuts
Ctrl+K command palette · Ctrl+1…9 screens · Ctrl+J journal · Ctrl+I (or Ctrl+/) AI panel · Ctrl+, settings.

## Privacy controls
Settings → Privacy & data: data-flow summary, cloud AI disclosure log, delete AI conversations, encrypted backup and
restore, export everything (CSV + manifest), delete all data, delete profile and key. Revoke phones in Phone sync,
Google in Google Health, ChatGPT/API key in AI providers.
