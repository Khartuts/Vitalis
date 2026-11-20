# Development Plan & Implementation Status

Legend: ✅ Implemented · 🧪 Tested (automated, in this environment) · 🌐 Blocked externally (needs account/hardware/approval) · ⏳ Not yet implemented

## Phases
| Phase | Scope | Status |
|---|---|---|
| 1 Feasibility | Official docs read 2026-11-20 (Samsung SDK v1.1.0, Health Connect 1.1.0, Google Health API v4, Sign in with ChatGPT, Responses API); ARCHITECTURE.md, DATA_ACCESS_MATRIX.md | ✅ |
| 2 Foundation | Monorepo, Electron shell, SQLCipher DB + migrations, Python engine, Android skeleton | ✅ 🧪 |
| 3 Real data | HC reader + serializer, Samsung SDK adapter, Samsung export, Google connector, normalization, dedupe, LAN + file sync | ✅ 🧪 (HC/Samsung on device: 🌐) |
| 4 Analytics | Sleep, recovery, energy, arousal, cardio, activity/load, baselines, quality | ✅ 🧪 |
| 5 Interface | 12 screens wired to engine queries, palette, shortcuts, themes, layouts, saved views | ✅ 🧪 (E2E) |
| 6 Habits & correlations | Journal, correlation lab, FDR screen, N-of-1 experiments | ✅ 🧪 |
| 7 AI | Responses provider (API key + ChatGPT plan), local provider, SIWC OAuth, read-only tools, consent, evidence | ✅ 🧪 (mocks) · real services 🌐 |
| 8 Reporting | Report builder, PDF (A4), CSV, JSON, all-data export, briefs | ✅ 🧪 |
| 9 Security & tests | See SECURITY.md / TESTING.md | ✅ 🧪 |
| 10 Packaging | Windows NSIS installer, Android debug + release APK, CI workflow, GitHub Releases on `v*` tags | ✅ 🧪 (installer installed and driven on a Windows Server 2022 CI runner) |

## Feature checklist
| Feature | Status | Notes |
|---|---|---|
| Encrypted storage (SQLCipher), DPAPI-wrapped keys, backups, one-click deletion | ✅ 🧪 | |
| Canonical model with provenance, raw retention, UTC + original offsets, DST | ✅ 🧪 | |
| Duplicate detection, conflict resolution, source priority | ✅ 🧪 | |
| Outliers, ranges, sensor gaps, freshness, source reliability | ✅ 🧪 | |
| Health Connect read-only permissions (per type, history, background) | ✅ (compiled) | on-device 🌐 |
| HC Changes API incremental sync, token expiry handling | ✅ (compiled) | on-device 🌐 |
| Aggregate-based steps/distance/active energy (no double counting) | ✅ 🧪 (desktop side) | |
| Samsung Health Data SDK adapter | ✅ (not compiled) | AAR login-gated 🌐; partner approval for distribution 🌐 |
| Samsung export import (ZIP/CSV, versioned adapter, preview) | ✅ 🧪 | real exports to validate 🌐 |
| QR pairing, pinned TLS, device tokens, revocation, idempotent batches | ✅ 🧪 | |
| Encrypted file transfer fallback (cross-language verified) | ✅ 🧪 | |
| Google Health API v4 OAuth (loopback + PKCE), sync, pagination, backoff | ✅ 🧪 (mocks) | real API 🌐 |
| Overview, Sleep Lab (planner, heatmap, hypnogram, SRI, debt) | ✅ 🧪 | |
| Recovery estimate with drivers; exploratory energy timeline | ✅ 🧪 | |
| Stress/arousal timeline, stress journal | ✅ 🧪 | |
| Cardio, activity, zones, TRIMP load | ✅ 🧪 | |
| Trends explorer, saved views, period comparison with CI | ✅ 🧪 | |
| Habit journal (custom habits, private flag, history) | ✅ 🧪 | |
| Correlation lab (Pearson/Spearman, CIs, effective N, lags, rolling, group tests, OLS-HAC, FDR) | ✅ 🧪 | |
| N-of-1 experiments (randomized blocks, permutation test) | ✅ 🧪 | |
| Insight engine + morning/weekly/monthly/quarterly briefs | ✅ 🧪 | |
| AI analyst (tools, consent preview, disclosure log, evidence chips, threads, explain-this-chart) | ✅ 🧪 | real providers 🌐 |
| Sign in with ChatGPT (dynamic registration, JWKS validation, refresh, revoke, usage UI) | ✅ 🧪 (mock AS) | real account 🌐 |
| Reports: PDF/CSV/JSON/PNG charts | ✅ 🧪 | |
| Synthetic demo mode (clearly labelled, separate profile) | ✅ 🧪 | |
| Croatian localization | ✅ partial | full translation ⏳ |
| Windows code signing | ⏳ | needs certificate 🌐 |
| ECG / glucose analytics, GPS routes | ⏳ (by design not in v1) | |

## Next steps (in order)
1. ~~Push to GitHub and verify the installer on a real Windows runner~~ — done (run 44783780670). Remaining: a person
   installs it on a Windows 11 desktop (interactive installer, SmartScreen, firewall prompt, real phone sync).
2. Install the APK on the user's phone; run the hardware checklist in TESTING.md; update DATA_ACCESS_MATRIX.md 🔍.
3. Download the Samsung SDK AAR; compile `src/samsung`; fix any API-name differences; test in developer mode.
4. Connect real accounts (ChatGPT / API key / Google) and confirm request/response compatibility.
5. Sign the Windows installer and the release APK with the user's own keys.

## Architecture decisions
See ARCHITECTURE.md §11 (D1–D8). Added during implementation:
* D9 — pandas 3 uses microsecond datetimes; all time arithmetic uses `total_seconds()` rather than int64 casts.
* D10 — Sleep sessions store the end offset (migration 2) so wake times are DST-correct.
* D11 — AndroidX pinned to versions supporting compileSdk 36 / AGP 8.13 (Compose BOM 2026.06.00, OkHttp 5.4.0).
* D12 — Windows runtime = official CPython NuGet build with packages in its own site-packages; engine runs with `-I`.
