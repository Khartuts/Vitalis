# VITALIS — Architecture

*Personal AI Health Intelligence. Windows 11 desktop app + Android companion.*
Document status: maintained alongside the code. Last revised 2026-11-20.

## 1. Guiding constraints

1. **Local-first.** All health data lives in one encrypted SQLite (SQLCipher 4) database per profile on the Windows PC. Nothing is uploaded unless the user explicitly sends a previewed AI request or connects a cloud source.
2. **Deterministic analytics first, AI second.** Every number shown or cited is computed by versioned Python code. The language model only *requests* computations through validated, read-only tools and interprets their results.
3. **Provenance everywhere.** Every stored measurement keeps its source app, device, original ID, original value/unit, original timezone offset, import method and raw-record reference.
4. **Honest availability.** A metric is "available" only if a source actually returned it. The UI distinguishes *measured / imported / calculated / estimated / user-entered / AI-interpreted*.
5. **Least privilege.** Read-only scopes and permissions; the renderer has no Node access; the Python engine has no internet access.

## 2. System overview

```
 Galaxy Watch6 ──(Samsung sync)──► Samsung Health (phone) ──┬─► Health Connect (on-device)
                                                            │
                                    Samsung Health Data SDK ┘ (developer mode / partner)
                                              │                         │
                                   ┌──────────▼─────────────────────────▼─────────┐
                                   │ VITALIS Companion (Android, Kotlin/Compose)  │
                                   │  • HC reader (read-only, per-type perms,     │
                                   │    history perm, Changes API tokens)         │
                                   │  • Samsung SDK adapter (optional module)     │
                                   │  • Pairing (QR) • Sync queue • File export   │
                                   └──────────┬──────────────────────┬────────────┘
                     HTTPS (TLS 1.3, self-signed cert pinned by        │ AES-256-GCM encrypted
                     SHA-256 fingerprint from QR) + device bearer      │ .vitalis-sync file (fallback)
                                              │                        │
┌─────────────────────────────────────────────▼────────────────────────▼──────────────────────┐
│ Windows PC                                                                                   │
│  ┌───────────────────────────── VITALIS Engine (Python 3.12) ────────────────────────────┐   │
│  │  stdio JSON-RPC bridge  ◄── only channel to the desktop app (no TCP listener)          │   │
│  │  LAN sync server (FastAPI/uvicorn, HTTPS) — started only while the user enables sync   │   │
│  │  ingest adapters → normalization → quality checks → SQLCipher DB                        │   │
│  │  features · baselines · recovery/energy/load/stress · sleep lab · cardio · activity     │   │
│  │  statistics (correlation, group tests, OLS, FDR) · N-of-1 experiments · insights        │   │
│  │  AI tool layer (read-only, Pydantic-validated, size-limited) · reports/exports           │   │
│  └───────────────────────────────────────▲───────────────────────────────────────────────┘   │
│                                          │ newline-delimited JSON-RPC over stdin/stdout      │
│  ┌──────────────────── Electron main process (TypeScript) ─────────────────────────────────┐ │
│  │  engine supervisor · DB key custody (safeStorage/DPAPI) · Zod-validated IPC handlers     │ │
│  │  AI orchestrator (OpenAI Responses via API key or Sign in with ChatGPT; local           │ │
│  │  OpenAI-compatible runtime) · Google Health OAuth (system browser, loopback, PKCE)       │ │
│  │  PDF rendering (printToPDF) · CSP · navigation/permission lockdown                       │ │
│  └───────────────────────────────────────▲─────────────────────────────────────────────────┘ │
│                                          │ contextBridge: typed, allow-listed API            │
│  ┌──────────────────── Renderer (React + Vite + Tailwind, sandboxed) ──────────────────────┐ │
│  │  12 screens · ECharts · TanStack Query · Zustand · Radix UI · cmdk palette · AI panel    │ │
│  └─────────────────────────────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
        ▲ HTTPS (OAuth 2.0 bearer, read-only scopes)          ▲ HTTPS (bearer: API key or ChatGPT OAuth)
   health.googleapis.com/v4                               api.openai.com/v1/responses
```

## 3. Monorepo layout

| Path | Contents |
|---|---|
| `services/engine/` | Python analytics engine (`vitalis_engine` package), tests, synthetic-data generator |
| `apps/desktop/` | Electron + React desktop app (main, preload, renderer), Vitest + Playwright tests, electron-builder config |
| `apps/android/` | Android companion (Gradle, Kotlin, Compose), unit tests, optional Samsung SDK module |
| `scripts/` | Reproducible build scripts (engine runtime bundling, installer, APK) |
| `docs/` | Supplementary diagrams and setup guides |

## 4. Process model and trust boundaries

| Component | Privileges | Talks to | Never does |
|---|---|---|---|
| Renderer | None (sandbox, `contextIsolation`, no `nodeIntegration`, strict CSP) | preload API only | network, filesystem, secrets |
| Preload | Exposes ~40 named, typed functions | `ipcRenderer.invoke` on allow-listed channels | arbitrary channel access |
| Main | Filesystem, network, OS keystore | engine (stdio), OpenAI, Google, browser for OAuth | send health data without a consent record |
| Engine | DB file access; LAN socket only when sync is enabled | main (stdio), paired phones (HTTPS) | outbound internet, SQL/code from AI |
| Companion | HC/Samsung read permissions, network to the paired PC only | paired desktop | write to Health Connect / Samsung Health |

The engine is started by main with the DB key delivered over stdin (never argv or env). If main exits, the engine exits on EOF. Each JSON-RPC method has a Pydantic parameter model; unknown methods are rejected.

## 5. Data model (canonical)

Implemented in `services/engine/vitalis_engine/db/schema.py` (migrations are versioned and append-only).

* `sources` — provider (health_connect, samsung_sdk, samsung_export, google_health, manual, synthetic), app package, device (manufacturer/model/type), reliability rank.
* `raw_records` — exact payload as received (JSON), payload hash, import batch, provider record ID. Retained for audit; never altered.
* `measurements` — one row per normalized value: stable `record_id` (UUIDv5 of provider + provider ID + metric + timestamp), `metric`, `value_num` / `value_text`, `unit`, `orig_value`, `orig_unit`, `start_utc`, `end_utc`, `utc_offset_min` (original), `local_date` (Europe/Zagreb by default), `provenance` (measured/imported/calculated/estimated/user_entered/ai_interpreted), `quality_flags` (bitmask), `source_id`, `raw_id`, `import_method`, `imported_at`, `updated_at`, `superseded_by` (dedup), `deleted_at` (provider deletion).
* `sleep_sessions`, `sleep_stages`, `exercise_sessions` — structured interval data with the same provenance columns.
* `daily_features` — per local date, per feature, with `algo_version`, inputs used/missing, completeness.
* `baselines`, `scores` (recovery/energy/load/stress), `insights` (with evidence JSON + dedup key).
* `journal_habits` (definitions), `journal_entries` (values; `private` flag excludes from cloud AI).
* `experiments`, `experiment_days`.
* `chat_threads`, `chat_messages`, `ai_disclosures` (exact payload previews sent to a provider, hashes, timestamps).
* `sync_devices` (paired phones, token hashes), `sync_batches`, `sync_cursors`, `import_batches`.
* `analysis_versions`, `preferences` (incl. source-priority policy, display timezone), `schema_migrations`.

**Time.** Instants are stored as UTC ISO-8601 with the original offset kept separately; "local day" assignment uses the user's display timezone through `zoneinfo` (DST-correct). Sleep is attributed to the local date of the *wake* time.

**Dedup / overlap.** Records are deduplicated (a) exactly by provider ID, (b) cross-provider by metric + overlapping interval + value tolerance. The losing record is kept and marked `superseded_by`; the user-configurable source-priority policy decides the winner (default: Samsung SDK > Health Connect (Samsung origin) > Google Health > exports > manual for the same metric). Step totals are never summed across overlapping sources.

## 6. Analytics engine

Versioned modules (`ALGORITHMS.md` documents each): daily features, robust baselines (rolling median + MAD; provisional ≥14 valid days, stable ≥28; configurable), Morning Recovery Estimate (`recovery.v1`), Daily Energy Estimate (`energy.v1`, exploratory), cardiovascular load (`load.trimp.v1`, Edwards zone TRIMP from HR samples), physiological-arousal index (`arousal.v1`, labeled as *not* psychological stress), sleep regularity (SRI), sleep debt (explicit assumptions), statistics (Pearson + Fisher CI, Spearman + bootstrap CI, Welch/Mann-Whitney with Hedges g, lagged correlation, OLS with HAC errors, Benjamini–Hochberg FDR, effective-N for autocorrelation), N-of-1 experiments (randomized block schedules, permutation tests), insight rules.

HRV is never computed from averaged HR. RMSSD/SDNN computation exists only for genuine beat-to-beat interval input (`hrv/ibi.py`, with artefact rejection) and is unused unless a source supplies IBIs.

## 7. AI architecture

* Providers: (1) **Sign in with ChatGPT** — documented open-source/local flow: `dynamic_agent_client` registration, PKCE + nonce, loopback `http://127.0.0.1:<port>/auth/callback`, scopes `openid profile email offline_access resource.invoke chatgpt.tokens.use.direct`, ID-token validation via JWKS, refresh with rotation, revocation on sign-out; requests use `POST https://api.openai.com/v1/responses` with `store:false, stream:true`, no `temperature`/`max_output_tokens`/`previous_response_id`, tools grouped in a `namespace`. (2) **OpenAI API key** — same Responses wire format. (3) **Local runtime** — OpenAI-compatible Chat Completions on `127.0.0.1` only (Ollama, LM Studio, llama.cpp server).
* The OpenAI DevKit (`@siwc/local`) is private (unpublished) and under a noncommercial license; VITALIS implements the documented protocol directly instead of vendoring it (see `SECURITY.md`).
* Tools (read-only, Pydantic-validated, max 400 days per call, max 2 000 points returned, no SQL, no code): `get_available_metrics, get_metric_history, get_sleep_summary, get_recovery_summary, get_stress_timeline, get_daily_activity, get_baseline, compare_periods, calculate_correlation, analyze_habit, run_experiment_analysis, get_data_quality, generate_chart_spec, create_report`.
* Consent: before any cloud request the user sees the exact JSON that will leave the device (system prompt + messages + tool results for this turn); approved payloads are logged to `ai_disclosures`. Private journal habits are filtered out of tool results for cloud providers.
* Every assistant answer carries `evidence` entries (metric, date range, tool call ID) rendered as clickable chips that open the corresponding chart.

## 8. Sync protocol (phone → PC)

1. Desktop enables sync → engine generates/loads a self-signed ECDSA P-256 certificate, starts HTTPS on the LAN interface (port 47821 by default) and shows a QR containing `{v, name, hosts[], port, fp(SHA-256 of cert DER), code (one-time, 10 min), fk (file key)}`.
2. Phone scans QR, connects pinning `fp` (no CA trust), `POST /v1/pair {code, device_name}` → `{device_id, token}` (256-bit; only SHA-256 stored on PC).
3. `POST /v1/batches` with `Authorization: Bearer`; each batch has `batch_id`, `seq`, record list. Server is idempotent per `batch_id` (replays acknowledged, not re-imported) → supports restarts and partial sync.
4. Phone advances its Health Connect Changes token / time cursor only after the PC acknowledges.
5. Fallback: phone writes `*.vitalis-sync` = AES-256-GCM(file key, JSON batch) for USB/cloud-drive transfer; PC verifies tag and imports with the same idempotency rules.
6. Tokens can be revoked per device; pairing codes expire; expired tokens return 401 → re-pair flow.

## 9. Security summary

See `SECURITY.md`. DB key: 256-bit random, wrapped by Electron `safeStorage` (Windows DPAPI). Backups: SQLCipher export re-keyed with a user passphrase (scrypt-derived). OAuth/API secrets: `safeStorage`-wrapped files. CSP: `default-src 'self'`; no remote code. No telemetry, analytics or ads SDKs.

## 10. Localization

All renderer strings go through `i18n/` dictionaries keyed by message ID (`en` complete; `hr` scaffolded with the same keys). Numbers/dates use `Intl` with the profile locale; units are converted at display time only.

## 11. Key decisions log

| # | Decision | Rationale |
|---|---|---|
| D1 | stdio JSON-RPC between Electron and Python instead of a loopback HTTP API | no listening socket for the most privileged channel; process-lifetime bound |
| D2 | FastAPI only for the phone-sync LAN server | needs a network listener; TLS + pinning + bearer tokens |
| D3 | SQLCipher via `sqlcipher3` wheels (win_amd64 + manylinux) | maintained, encryption at rest, no native build step |
| D4 | Windows Python runtime = official CPython build (NuGet `python` 3.12.10) + `win_amd64` wheels in its own site-packages; engine started with `python -I -m vitalis_engine` | reproducible cross-build from Linux; no user Python install; isolated from user env |
| D5 | Health Connect `connect-client` 1.1.0 (stable), AGP 8.13.2, compileSdk 36 | alpha 1.2.0 avoided; newest AndroidX requiring compileSdk 37/AGP 9.1 deliberately not used |
| D6 | Samsung SDK adapter compiled only when the AAR is present | AAR download requires a Samsung account; build stays green without it |
| D7 | AI orchestration in Electron main | credentials never leave main; engine stays offline |
| D8 | ECharts for charts | large-series performance, zoom/pan (dataZoom), PNG export |
