# Limitations (honest list)

## Data availability
* **Samsung does not expose raw HRV, inter-beat intervals, PPG or its stress score** through Health Connect or the
  Samsung Health Data SDK type list. VITALIS therefore shows HRV only if another source (e.g. Google Health with a
  Fitbit/Pixel device, or a Health Connect writer that provides RMSSD) supplies it. It never computes HRV from
  averaged heart rate. Recovery then relies on resting HR + sleep (completeness is shown).
* Which types Samsung Health actually writes into Health Connect for a Galaxy Watch6 (resting HR, respiratory rate,
  skin temperature, HRV) **has not been verified on your device** — run the companion's availability probe.
* Health Connect returns only 30 days before the first grant unless "Access past data" is granted.
* Samsung Health exports change format between versions; the export adapter recognizes known tables by column
  suffix and shows a preview, but unknown formats are skipped, not guessed. Stage codes 40001–40004 are assumed.
* Wrist sleep staging, SpO2 and calories are estimates with known error; VITALIS labels them as such.

## External integrations not verified against real services
* **Sign in with ChatGPT**: implemented from OpenAI's open-source plan-usage documentation (2026-11) and tested against
  a mock authorization server. Not exercised with a real account. OpenAI documents it as available to open-source
  and locally hosted apps; eligibility (Plus/Pro, workspace policy, region) is decided by OpenAI. The DevKit was not
  vendored (noncommercial license, unpublished package).
* **OpenAI API key** path: tested against a mock Responses server only. The `namespace` tool grouping follows the
  current function-calling guide; if a model rejects it, flat function tools would be needed.
* **Google Health API v4**: tested against mocks. Testing-mode tokens expire after 7 days. Not useful without a
  Google Health account that has data.
* **Local models**: tool-calling quality varies; only OpenAI-compatible `/v1/chat/completions` with tools is supported.

## Samsung Health Data SDK adapter
* Written from public guides; the AAR and API reference require a Samsung login, so the adapter was **never
  compiled**. It uses reflection for undocumented field names. Expect import/name fixes on first build.
* Developer mode is for personal testing; distributing the app requires Samsung partnership approval.

## Android companion
* Not run on a physical phone in this build (no device attached). Health Connect behaviour, permission UI, background
  WorkManager scheduling and rate limits are unverified on hardware.
* Built with compileSdk 36 / AGP 8.13; the newest AndroidX/Compose releases (requiring compileSdk 37 / AGP 9.1) were
  intentionally not used.
* The delivered debug APK is signed with the build machine's debug key (updates require reinstall unless you sign
  with your own key).

## Windows
* The installer is **unsigned** (SmartScreen warning).
* The installer was built but **not installed or launched on real Windows** in this build; a silent install under Wine did not complete.
* The bundled NumPy/SciPy stack could not be executed under Wine in the build container; Python, SQLCipher, Pydantic
  and cryptography were verified there. Full verification on Windows is defined in CI (`.github/workflows/build.yml`)
  but has not run yet (no GitHub remote).
* Phone sync listens on TCP 47821 on your LAN while enabled; Windows Firewall must allow it on Private networks.

## Analytics
* Recovery, energy and arousal are **original, unvalidated** heuristics built on established concepts; useful for
  personal trends, not for clinical or training decisions. Energy parameters are hand-set.
* Resting HR is estimated from overnight samples; with sparse sampling (e.g. every 10 min) it is coarser.
* Correlations in personal data are confounded by routine; FDR control reduces but does not remove false findings.
* N-of-1 experiments are unblinded.

## Product
* Croatian localization is partial (navigation and key labels); other strings fall back to English.
* No auto-update mechanism (intentional: no network services).
