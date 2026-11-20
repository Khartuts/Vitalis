# VITALIS — Personal AI Health Intelligence

A local-first Windows desktop app plus an Android companion that turn your Galaxy Watch6 / Samsung Health data
(via Health Connect, the Samsung Health Data SDK, Samsung exports or Google Health) into deep, honest analysis:
sleep lab, recovery and energy estimates, physiological-arousal tracking, cardio and activity analytics, a habit
journal, a statistics-grade correlation & N-of-1 experiment lab, reports — and an AI analyst that can only answer
from your data through validated, read-only tools, with an exact preview of anything sent to the cloud.

> VITALIS is wellness software, **not a medical device**. It does not diagnose, and its estimates are not clinical measurements.

## What is in this repository

| Path | What |
|---|---|
| `services/engine` | Python 3.12 analytics engine: SQLCipher-encrypted storage, ingestion adapters, analytics, statistics, AI tools, LAN sync server, 49 tests |
| `apps/desktop` | Electron 45 + React 19 + TypeScript desktop app (12 screens, AI analyst, Google Health connector, PDF reports), unit + end-to-end tests |
| `apps/android` | Kotlin/Compose companion: Health Connect reader, optional Samsung SDK adapter, QR pairing, pinned-TLS sync, encrypted file export |
| `scripts` | Reproducible build scripts (Windows runtime bundle, smoke test) |
| docs | [ARCHITECTURE](ARCHITECTURE.md) · [DATA_ACCESS_MATRIX](DATA_ACCESS_MATRIX.md) · [ALGORITHMS](ALGORITHMS.md) · [SECURITY](SECURITY.md) · [TESTING](TESTING.md) · [INSTALLATION](INSTALLATION.md) · [USER_GUIDE](USER_GUIDE.md) · [LIMITATIONS](LIMITATIONS.md) · [DEVELOPMENT_PLAN](DEVELOPMENT_PLAN.md) |

## Quick start (developers)

```bash
# 1. Engine
cd services/engine && python3.12 -m venv .venv && .venv/bin/pip install -r requirements.lock.txt pytest httpx && .venv/bin/pip install -e . && .venv/bin/pytest -q
# 2. Desktop (uses the engine venv in development)
cd ../../apps/desktop && npm ci && npm run build && npm start
# 3. Android companion
cd ../android && ./gradlew :app:assembleDebug        # app/build/outputs/apk/debug/app-debug.apk
# 4. Windows installer (on Windows, or Linux with wine + wine32)
scripts/build-windows-runtime.sh && cd apps/desktop && npx electron-builder --win nsis --x64 --publish never
```

End users: download the installer and APK from [Releases](https://github.com/Khartuts/Vitalis/releases) and see
[INSTALLATION.md](INSTALLATION.md).

## Status at a glance (details: [DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md))

| Area | State |
|---|---|
| Encrypted storage, ingestion, dedupe, analytics, statistics, insights, reports | Implemented and tested (synthetic + hand-built fixtures) |
| Desktop app (all 12 screens, demo mode, journal, lab, reports/PDF) | Implemented; end-to-end tested in the real Electron app (23/23 steps) |
| AI analyst: OpenAI API key, Sign in with ChatGPT, local OpenAI-compatible runtime | Implemented; tested against mock servers. **Not yet verified against the real OpenAI services** (needs your account) |
| Google Health API v4 connector | Implemented; tested against mocks. **Not yet verified against the real API** (needs your Google Cloud project) |
| Android companion (Health Connect, pairing, sync, file export) | Builds (APK), unit-tested; **not yet run on a physical phone** (none attached to the build machine) |
| Samsung Health Data SDK adapter | Written against public docs; **not compiled** — the SDK AAR requires your Samsung developer login |
| Windows installer | Built and tested in CI on Windows Server 2022: silent install, bundled engine, installed app through demo profile + every screen. **Not yet run by a person on a Windows 11 desktop** (unsigned → SmartScreen warning) |
