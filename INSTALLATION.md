# Installation

## Downloads

Installers are published on the repository's **Releases** page: <https://github.com/Khartuts/Vitalis/releases>
(`VITALIS-Setup-<version>-x64.exe`, the companion APKs and `SHA256SUMS.txt`). They are built and tested by the
`build-and-test` GitHub Actions workflow, which publishes a release when a version tag (`v*`) is pushed, or when the
repository owner runs it from **Actions → build-and-test → Run workflow** with a release tag such as `v0.1.0`. Every run also keeps the installer and
APKs as downloadable workflow artifacts (Actions → the run → *Artifacts*; needs a GitHub login, kept 90 days).

Verify a download (PowerShell): `Get-FileHash .\VITALIS-Setup-0.1.0-x64.exe -Algorithm SHA256` and compare with
`SHA256SUMS.txt`.

## 1. Windows desktop app (end users)

1. Download and run `VITALIS-Setup-0.1.0-x64.exe`. The installer is **not code-signed** yet, so Windows SmartScreen will say
   "Windows protected your PC" → *More info* → *Run anyway* (see "Code signing" below).
2. Choose an install folder (per-user install, no admin rights needed). Everything the app needs — including its own
   Python runtime — is bundled; you do not need to install Python or Node.js.
3. Start VITALIS → create your **private profile** (name, optional birth year for heart-rate zones, time zone —
   default Europe/Zagreb) or a **demo profile** with clearly-labelled synthetic data.
4. Your data is stored in `%APPDATA%\vitalis-desktop\profiles\` as an encrypted SQLCipher database. Its key is
   protected by Windows (DPAPI via Electron safeStorage) and never written to disk in plain text.

### Without the installer (portable ZIP)
`VITALIS-<version>-win-x64-portable.zip` contains the same app. Right-click → *Extract All…* to a folder you own
(e.g. `C:\Users\<you>\VITALIS`), then run `VITALIS.exe` from it. Data goes to the same place as with the installer.
To remove it, delete the folder (your profiles stay in `%APPDATA%\vitalis-desktop` until you delete them in the app).

### "NSIS Error — Error launching installer"
The installer could not open its own file when it started. The file on GitHub is intact (CI installs that same file
on Windows), so the cause is how it is being opened on the PC:
1. **Do not run it from inside the ZIP.** Workflow artifacts download as a `.zip`: right-click → *Extract All…*,
   then run the extracted `.exe`.
2. **Check the size and checksum.** The file must be about 204 MB; compare `Get-FileHash <file> -Algorithm SHA256`
   with `SHA256SUMS.txt` on the release. If it differs, download it again.
3. **Antivirus.** Unsigned installers are sometimes held or quarantined while being scanned. Look in *Windows
   Security → Virus & threat protection → Protection history* (or your antivirus' quarantine), restore the file if it
   was quarantined, then run it again from a normal folder such as Downloads or the Desktop.
4. Right-click the file → *Properties*; if there is an **Unblock** box at the bottom, tick it → OK.
5. Still failing: use the portable ZIP above.

### Code signing (recommended before sharing the installer)
Unsigned installers trigger SmartScreen. To sign: obtain an Authenticode certificate (OV/EV or Azure Trusted
Signing), set `win.signAndEditExecutable: true` and the certificate environment variables documented by
electron-builder (`CSC_LINK`, `CSC_KEY_PASSWORD` or Azure signing options), then rebuild on Windows.

## 2. Android companion

Requirements: Android 9+ (Health Connect), Samsung Health ≥ 6.30.2 for the optional Samsung SDK.

1. Copy the companion APK from the Releases page (`VITALIS-Companion-<version>-debug.apk`, or your own signed
   release build) to the phone and open it; allow "Install unknown apps" for
   your file manager when asked.
2. **Health Connect:** on Android 14+ it is built in (Settings → Security & privacy → Privacy → Health Connect);
   on Android 13 and lower install "Health Connect" from Google Play.
3. **Let Samsung Health share into Health Connect:** Samsung Health → ⋮ → Settings → Health Connect → allow the
   data types you want Samsung Health to *write* to Health Connect.
4. On the PC: VITALIS → Data Sources & Settings → **Phone sync** → *Enable phone sync* (Windows may ask to allow
   VITALIS on **private** networks — allow it) → *Pair a phone*.
5. In the companion: *Scan QR code* (camera permission is used only for this) or paste the pairing text.
6. *Request selected permissions* → approve the data types you want (all optional). Approve **"Access past data"**
   to sync more than 30 days of history.
7. *Sync now*. Optionally enable hourly background sync (needs "read in the background").
8. *Run availability probe* to see exactly which data your phone shares: records per type in the last 30 days, the
   apps that wrote them and the **oldest** record VITALIS can read. The report also appears on the PC under
   Sources & quality. "0 records" for everything means Samsung Health is not sharing into Health Connect (step 3).
9. *Re-read all history* reads everything again from the oldest record — use it after granting "Access past data"
   or after Samsung Health starts sharing. Records the PC already has are recognized and skipped.

**Full VITALIS on the phone (preview).** `VITALIS-Phone-<version>-preview-arm64.apk` is the companion plus the
complete analysis running on the phone (no PC needed). Uninstall VITALIS Companion first, install the preview APK,
grant the Health Connect permissions, then tap **Open VITALIS**. The first opening imports your Health Connect history
(this can take a few minutes); later openings import only what is new. Pair with the PC again if you also use the
desktop app.

**Getting older history.** The companion can only read what Samsung Health has written into Health Connect. If the
probe's "oldest" date is recent although Samsung Health shows older data, that history is not in Health Connect.
Bring it over with Samsung Health's own export: Samsung Health → ⋮ / menu → Settings → *Download personal data* →
Download. It saves CSV files in a folder under `Download/Samsung Health/` on the phone; compress that folder to a ZIP
(My Files → long-press the folder → *Compress*), copy it to the PC and import it under Data Sources & Settings →
Imports → *Samsung Health export (ZIP/CSV)*. You see a preview before anything is imported; data that also arrived
through Health Connect is de-duplicated by source priority.

**Updating the companion.** Builds from GitHub are signed with a key that changes per build, so Android will not
install a new version over the old one: uninstall VITALIS Companion first, install the new APK, pair again and
re-grant the permissions. Your data on the PC is not affected.

No shared network? Use *Export encrypted file* in the companion and import the `.vitalis-sync` file on the PC
(Settings → Imports). The file is AES-256-GCM encrypted with the key from your pairing QR.

### Build the APK yourself
Install Android Studio (or the command-line tools) with **SDK Platform 36** and **Build-Tools 36**, JDK 17+:
```bash
cd apps/android && ./gradlew :app:assembleDebug
```
For a stable signature across updates, create your own keystore and `apps/android/keystore.properties`:
```
storeFile=/absolute/path/vitalis.jks
storePassword=…
keyAlias=vitalis
keyPassword=…
```
then `./gradlew :app:assembleRelease`. (The debug APK delivered with this build is signed with the build
machine's debug key; installing a build signed with another key requires uninstalling first.)

## 3. Samsung Health Data SDK (optional, personal developer mode)

The Health Connect path works without this. The SDK adds Samsung-only data (sleep score, Energy Score, Samsung's
own skin-temperature and sleep-stage objects).

1. Sign in at developer.samsung.com → Health → Samsung Health Data SDK → download **v1.1.0**. Put
   `samsung-health-data-api.aar` into `apps/android/app/libs/` and rebuild — the Gradle build detects it and
   compiles `src/samsung` instead of the stub. (This adapter was written from the public guides without the AAR;
   expect small import/name fixes on first compile — see LIMITATIONS.md.)
2. On the phone: Samsung Health → ⋮ → Settings → About Samsung Health → tap the version line **10+ times** →
   *Developer mode (Samsung Health Data SDK)* → agree → turn on **Developer Mode for Data Read**.
   Samsung states this mode is for testing only, not for app users.
3. In the companion: *Connect Samsung Health* and approve the permissions.
4. **Distribution** to anyone else requires Samsung **partnership approval** with your app's package name and
   release-signing SHA-256; otherwise the SDK returns error 2003 / AuthorizationException.

## 4. Google Health API (optional)

Only useful if you also have Fitbit / Pixel Watch / Google Health data. Follow the in-app wizard
(Settings → Google Health): link the Google Health mobile app to your Google Account, create a Google Cloud project,
enable **Google Health API**, set the OAuth consent screen to External/Testing with yourself as test user, add the
three read-only scopes, create an **OAuth client ID of type "Desktop app"**, paste it into VITALIS, *Connect*.
Testing-mode refresh tokens expire after 7 days (reconnect when asked).

## 5. AI analyst (optional — all analytics work without it)

* **ChatGPT plan (Sign in with ChatGPT):** Settings → AI providers → *Continue with ChatGPT*. Your browser opens
  OpenAI's sign-in; approve VITALIS's use of your plan. Requires an eligible ChatGPT Plus/Pro account and that
  OpenAI's open-source plan-usage flow is available to you.
* **OpenAI API key:** create a key at platform.openai.com → paste it (stored encrypted). Billed to your API account.
* **Local model:** install Ollama / LM Studio, pull a tool-calling model, enable *Local model* with
  `http://127.0.0.1:11434/v1` (Ollama default). Nothing leaves your PC.

## 6. Developer setup

| Tool | Version used |
|---|---|
| Python | 3.12 (engine), `services/engine/requirements.lock.txt` |
| Node.js | 22.x, npm 10 |
| Electron / electron-builder | 45.4.1 / 26.18.0 |
| Android | AGP 8.13.2, Gradle 8.14.3 (wrapper), Kotlin 2.4.21, compileSdk 36, minSdk 28, Health Connect client 1.1.0 |
| Windows runtime | CPython 3.12.10 (official NuGet `python` package) + win_amd64 wheels via `scripts/build-windows-runtime.sh` |

Building the Windows installer on Linux needs `wine` **and** `wine32` (the NSIS stub is 32-bit). In the reference
container, Wine had to be launched with a minimal environment (see TESTING.md).
