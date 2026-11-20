# Testing

All results below were produced in the build environment on 2026-11-20 (Linux x86_64 container, Xvfb). Nothing
listed as "not run" was run.

## Summary

| Suite | Command | Result |
|---|---|---|
| Engine (pytest) | `cd services/engine && .venv/bin/pytest -q` | **49 passed** |
| Desktop unit (Vitest) | `cd apps/desktop && npx vitest run` | **13 passed** |
| Desktop end-to-end (real Electron + Playwright, Xvfb) | `xvfb-run -a node tests/e2e/app.e2e.mjs` | **23/23 steps passed**, 0 console errors |
| Android unit (JUnit) | `cd apps/android && ./gradlew :app:testDebugUnitTest` | **5 passed** |
| Android lint | `./gradlew :app:lintDebug` | 0 errors (warnings: newer versions available; intentional custom TrustManager for pinning) |
| Engine smoke (stdio protocol) | `python scripts/smoke_engine.py <python>` | passed with the dev interpreter |
| Windows bundle under Wine 9 | Windows CPython 3.12.10 + SQLCipher 4.20 + Pydantic + AES-GCM | **passed** |
| Windows bundle NumPy/SciPy under Wine | — | **could not run**: Wine 9.0 lacks `ucrtbase.crealf`; covered by the Windows CI job (not yet executed — needs a GitHub remote) |
| Windows installer build | `npx electron-builder --win nsis --x64` | built `VITALIS-Setup-0.1.0-x64.exe` (exit 0) |
| Windows installer silent install under Wine | `wine VITALIS-Setup-0.1.0-x64.exe /S` | **not verified**: did not finish within 15 min under Wine; covered by the CI job `desktop-and-installer` on a real Windows runner (not yet executed) |

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

On Windows 11: install the EXE, launch, create a profile, enable phone sync (allow firewall prompt on Private
networks), pair, sync, export PDF; run CI job `desktop-and-installer`.

With accounts: Sign in with ChatGPT (eligible Plus/Pro), an OpenAI API key, a Google Cloud OAuth client + Google
Health account. None were available in the build environment.

## Reproducing the E2E run
```bash
cd apps/desktop && npm run build
xvfb-run -a -s "-screen 0 1700x1100x24" node tests/e2e/app.e2e.mjs   # screenshots in $E2E_SHOTS or a temp dir
```
The E2E run uses `VITALIS_TEST=1` hooks that exist only in unpackaged builds: an explicit insecure test keystore (the
container has no OS keyring), a mock OpenAI base URL, and auto-answered save dialogs.

## Notes on the build container
* Maven Central rate-limited parallel downloads (HTTP 429); Gradle was run with `--max-workers=1` and retried.
* Wine 9.0 crashed with the session environment; it works with `env -i` (see `scripts` usage in this file's history)
  and needs `wine32` for the NSIS stub.
