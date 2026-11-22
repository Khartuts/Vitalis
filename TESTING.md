# Testing

Results below come from two places, both on 2026-11-20: the build environment (Linux x86_64 container, Xvfb) and
GitHub Actions on GitHub-hosted runners (workflow `build-and-test`, run 44783780670, commit `cbc188a`). Nothing
listed as "not run" was run.

## Summary

| Suite | Command | Result |
|---|---|---|
| Engine (pytest) | `cd services/engine && .venv/bin/pytest -q` | **62 passed** (v0.5.0, incl. body/bedroom temperature and journal sync) |
| Engine on the Android-compatible stack (Python 3.10, NumPy 1.23, pandas 2.1, SciPy 1.8, statsmodels 0.14, scikit-learn 1.3) | `PYTHONPATH=. python3.10 -m pytest` with `requirements.android-compat.txt` | **62 passed** locally (v0.5.0; CI job `engine-tests-android-stack`) |
| Desktop unit (Vitest) | `cd apps/desktop && npx vitest run` | **17 passed** (v0.2.0) |
| Desktop end-to-end (real Electron + Playwright, Xvfb) | `xvfb-run -a node tests/e2e/app.e2e.mjs` | **28/28 steps passed** (v0.4.0, incl. Explain on every analysis screen, the Temperature screen with a personal suggestion, background colours and the phone-width layout), 0 console errors |
| Android unit (JUnit) | `cd apps/android && ./gradlew :app:testDebugUnitTest` | **9 passed** (v0.5.0, incl. the phone AI client against MockWebServer) |
| Android lint | `./gradlew :app:lintDebug` | 0 errors (warnings: newer versions available; intentional custom TrustManager for pinning) |
| Engine smoke (stdio protocol) | `python scripts/smoke_engine.py <python>` | passed with the dev interpreter |
| Windows bundle under Wine 9 | Windows CPython 3.12.10 + SQLCipher 4.20 + Pydantic + AES-GCM | **passed** |
| Windows bundle NumPy/SciPy under Wine | — | could not run (Wine 9.0 lacks `ucrtbase.crealf`); superseded by the Windows CI rows below |

### GitHub Actions (run 44783780670)

| Job / step | Runner | Result |
|---|---|---|
| Engine tests | ubuntu-24.04 and windows-2022 | **49 passed** on each |
| Bundled Windows runtime: build + self-test (`import numpy, scipy, pandas, sqlcipher3, vitalis_engine`) | windows-2022 | passed |
| Engine smoke test with the bundled interpreter (stdio JSON-RPC, 45-day demo) | windows-2022 | passed |
| Desktop typecheck + unit tests (engine bridge uses the bundled Windows interpreter) | windows-2022 | **14 passed** |
| Desktop end-to-end (real Electron, mock AI server) | windows-2022 | **23/23 steps passed**, 0 console errors |
| NSIS installer build | windows-2022 | `VITALIS-Setup-0.1.0-x64.exe` built |
| Silent install (`/S`), then smoke test of the **installed** engine | windows-2022 | passed |
| Installed app driven by `tests/e2e/installed.e2e.mjs` (packaged build, DPAPI keystore, bundled engine, demo profile, all screens) | windows-2022 | **15/15 steps passed** |
| Android unit tests, lint, debug + release APKs | ubuntu-24.04 | passed |

`windows-2022` is Windows Server 2022, not a Windows 11 desktop; see "Must be performed…" below for what remains.

### Phone app on an Android emulator (job `phone-app`, Android 14 x86_64, v0.3.0)

| Test | What it checks | Result |
|---|---|---|
| Android builds of `pydantic_core` 2.50.5 and `sqlcipher3` 0.6.5 (arm64-v8a, x86_64) | Chaquopy build-wheel, Python 3.10 | built |
| `EngineOnDeviceTest` | real engine in the app: encrypted DB (no plaintext header, wrong key refused), 60 days of demo data + recompute, overview, explain.data, correlation, no LAN server | **passed** — open 2.7 s, demo.load 3.9 s, overview 0.1 s |
| `WebAppOnDeviceTest` | the bundled phone screens in the WebView, through the origin-restricted bridge: empty state → one imported night → dashboard → Sleep Lab → phone settings (status via `phone:*` channels) → Temperature | passed in v0.3.0; v0.4.0 in CI |

| `AnalystOnDeviceTest` (v0.5.0) | phone AI analyst end to end against a local HTTPS mock of the Responses API: consent before each round (nothing sent before approval), read-only engine tool, disclosure log, stored answer | in CI |
| `EngineOnDeviceTest` journal part (v0.5.0) | journal entries exchanged between two on-phone engines (vitalis.journal.v1) | in CI |

Not run: a physical phone (performance with years of real data, Health Connect permission screens, battery), Samsung SDK,
the real OpenAI service from the phone, journal sync between a real phone and PC.

## What the tests cover (requirement → test)

| Requirement | Where |
|---|---|
| Encrypted DB, wrong key rejected, header not plaintext | `test_db_time_units.py::test_database_is_encrypted_and_rejects_wrong_key` |
| Migrations idempotent; v1 → v2 upgrade keeps data | `test_migrations_idempotent`, `test_upgrade_from_v1_database` |
| Encrypted backup/restore, wrong passphrase | `test_backup_restore_roundtrip_and_wrong_passphrase` |
| Time zones, offsets, DST day lengths (23 h/25 h) | `test_offsets_and_instants`, `test_dst_day_lengths_zagreb`, `test_local_date_uses_original_offset_then_display_tz` |
| DST night (session split + spanning the change) | `test_session_merging_naps_and_dst_night`, `test_single_session_across_dst_without_end_offset` |
| Unit conversion, incompatible units refused | `test_unit_conversions` |
| Health Connect mapping, provenance, raw retention, unsupported types | `test_health_connect_mapping_and_provenance` |
| Duplicate records, idempotent re-import, updates, deletions | `test_reimport_is_idempotent_and_updates_and_deletions`, `test_same_batch_id_not_imported_twice` |
| Invalid sensor values, future timestamps, inferred TZ | `test_quality_flags_range_future_and_tz`, `test_malformed_record_rejected_but_raw_kept` |
| Cross-source dedupe + configurable priority; no double-counted steps | `test_cross_provider_dedupe_respects_priority`, `test_steps_not_double_counted_across_providers` |
| Google Health v4 shapes (from the REST reference), filters | `test_google_health_shapes_from_reference_docs`; desktop `Google Health connector` (pagination, 429 + Retry-After, invalid_grant/7-day expiry) |
| Samsung SDK mapping, unmapped fields | `test_samsung_sdk_mapping_and_unmapped_fields` |
| Samsung export format variants, preview, stage attachment | `test_samsung_export_preview_and_import_with_schema_variants` |
| Sleep metrics, latency/WASO/awakenings, no inferred stages | `test_sleep_metrics_from_stages`, `test_no_stages_means_no_inferred_stage_metrics` |
| Resting HR, HR zones / TRIMP | `test_rhr_from_sleep_hr_and_hr_zones` |
| Baseline thresholds & SD floor; recovery insufficient-data logic | `test_baseline_status_thresholds`, `test_recovery_requires_physiology_and_sleep` |
| Correlation CIs, small samples, effective N, BH-FDR, group tests, conditions | `test_correlation_*`, `test_effective_n_*`, `test_benjamini_hochberg_reference_values`, `test_group_comparison_and_hedges_g`, `test_time_condition_parsing` |
| HRV only from beat-to-beat intervals | `test_hrv_only_from_beat_intervals` |
| Experiments (balanced, reproducible schedules; analysis) | `test_experiment_schedule_*`, `test_experiment_analysis_on_demo` |
| 90-day synthetic E2E: built-in effects recovered (late caffeine → less deep sleep; alcohol → higher RHR; stress ↔ arousal) | `test_e2e_demo.py::test_known_effects_are_recovered` |
| Missing data explicit (no imputation; recovery "insufficient") | `test_missing_data_is_explicit` |
| Large dataset (365 days import + recompute < 120 s) | `test_large_dataset_performance` |
| AI tool validation (unknown fields, SQL injection attempt, unknown tool/metric, range limits), privacy filtering, no fabricated values for empty metrics, evidence IDs | `test_ai_tools_validation_privacy_and_no_fabrication` |
| Sync: pairing (one-time code), auth, replay idempotency, revoked token, bad JSON/schema, rate limit | `test_sync_rpc.py` |
| Real TLS listener: pinned fingerprint matches, plain HTTP refused | `test_real_tls_listener_with_pinned_fingerprint` |
| File-sync encryption + tamper detection (Python) / decrypt desktop file in Kotlin | `test_file_sync_encryption_and_tamper_detection`, Kotlin `decryptsDesktopProducedFile` |
| Engine process: protocol errors, EOF shutdown, wrong key fatal | `test_rpc_process_protocol`, `test_rpc_wrong_key_is_fatal` |
| IPC allow-list; local AI loopback only; vault refuses unprotected storage; profile deletion destroys key | desktop `IPC contract`, `Vault & profiles` |
| Responses streaming, function calls, usage-limit error codes, interrupted streams; chat-completions tool streaming | desktop `SSE and providers` |
| Consent gate per round (nothing sent if declined), disclosure log | desktop `Orchestrator`; E2E AI step (2 consent dialogs, payload has `store:false`, no API key, no private habits) |
| Sign in with ChatGPT protocol (dynamic registration, PKCE, loopback, ID-token signature + nonce, serialized refresh, revocation) | desktop `Sign in with ChatGPT` (mock authorization server with locally signed JWTs) |
| Real app workflows: demo profile, all 12 screens, journal save, correlation lab, AI answer with clickable evidence, PDF export, theme, palette, new real profile, phone pairing + pinned-TLS batch upload → data shown | `tests/e2e/app.e2e.mjs` |

## Synthetic data
`vitalis_engine/synthetic.py` generates ≥ 90 days (default 120 in the app): sleep sessions with stages, HR
(10-min, 1-min in workouts), SpO2, respiratory rate, HRV, skin-temperature deltas, hourly steps, workouts, weight,
journal entries (~12 % unlogged days), a DST transition, an illness episode, nights without the watch, a 6-hour HR
gap, rare optical HR spikes, and documented habit effects. It is only loadable into a profile flagged `is_demo`;
every record is provenance `synthetic`, source `vitalis.synthetic.demo`; the UI shows a permanent banner.

## Must be performed on physical hardware (not possible here)
On the user's phone (Galaxy Watch6 paired):
1. Install the APK; open; confirm Health Connect status "Available".
2. Request permissions; deny some; confirm the app keeps working and skips denied types.
3. Grant "Access past data"; *Sync now*; confirm > 30 days arrive on the PC.
4. Run the availability probe; record which types have data and their origins — update DATA_ACCESS_MATRIX.md 🔍 items.
5. Kill the app mid-sync; re-run; confirm no duplicates (desktop import history shows duplicates, not inserts).
6. Revoke the device on the PC; confirm the phone reports "pairing revoked".
7. Toggle airplane mode; export an encrypted file; import it on the PC.
8. Enable background permission + hourly sync; confirm WorkManager runs (Settings → Apps → Battery usage, or logcat).
9. Samsung SDK: build with the AAR, enable developer mode, connect, sync; check sleep score/Energy Score arrive.

On a Windows 11 PC (the CI covers silent install and the installed app on Windows Server 2022): run the interactive
installer (SmartScreen, install-folder page), create a real profile, enable phone sync (allow the firewall prompt on
Private networks), pair a real phone over Wi-Fi, sync, export a PDF through the real save dialog, uninstall.

With accounts: Sign in with ChatGPT (eligible Plus/Pro), an OpenAI API key, a Google Cloud OAuth client + Google
Health account. None were available in the build environment.

## Reproducing the E2E runs
```bash
cd apps/desktop && npm run build
xvfb-run -a -s "-screen 0 1700x1100x24" node tests/e2e/app.e2e.mjs   # Linux; on Windows run node directly
node tests/e2e/installed.e2e.mjs "%LOCALAPPDATA%\Programs\VITALIS\VITALIS.exe"   # Windows, after installing
```
Screenshots go to `$E2E_SHOTS` or a temp dir; the CI uploads them as the `windows-test-screenshots` artifact.
The E2E run uses `VITALIS_TEST=1` hooks that exist only in unpackaged builds: an explicit insecure test keystore (the
container has no OS keyring), a mock OpenAI base URL, and auto-answered save dialogs.

## Notes on the build container
* Maven Central rate-limited parallel downloads (HTTP 429); Gradle was run with `--max-workers=1` and retried.
* Wine 9.0 crashed with the session environment; it works with `env -i` (see `scripts` usage in this file's history)
  and needs `wine32` for the NSIS stub.
