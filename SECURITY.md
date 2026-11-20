# Security & Privacy

## Threat model (personal, single user)
Protect health data at rest on the PC and phone, in transit on the local network, and against unintended disclosure
to cloud providers; resist a compromised/malicious renderer, a hostile device on the LAN, and prompt-injected or
misbehaving AI models.

## Data at rest
* **Desktop:** one SQLCipher 4 database per profile (AES-256, HMAC per page). Raw 256-bit key, generated with a
  CSPRNG, wrapped with Electron `safeStorage` (Windows DPAPI). On Linux the weak `basic_text` backend is refused.
  If OS protection is unavailable, VITALIS refuses to create profiles rather than storing keys in plain text.
* Key delivery to the engine: over **stdin** only (never argv/env), then the engine keeps it in memory.
* **Backups:** SQLCipher export re-keyed with scrypt(passphrase, 16-byte salt, N=2^15, r=8, p=1); passphrase ≥ 10 chars.
* **Deletion:** profile deletion overwrites the DB/WAL/SHM with random bytes, deletes them and destroys the wrapped
  key. *Delete all data* clears every table and VACUUMs.
* **Phone:** pairing token, certificate fingerprint, file key and sync cursors are AES-GCM encrypted with a
  non-exportable Android Keystore key; app data is excluded from cloud backup and device transfer.

## Data in transit
* **Phone → PC:** HTTPS (TLS 1.2/1.3) to a self-signed ECDSA P-256 certificate whose SHA-256 fingerprint the phone
  pins (from the QR). No CA trust, no cleartext (network security config forbids it). One-time 8-digit pairing code,
  10-minute expiry, rate-limited (10 attempts / 10 min / IP). Per-device 256-bit bearer tokens; only SHA-256 stored;
  revocable. 48 MB request cap; idempotent batches; internal errors never leak details. The listener runs only while
  the user enables phone sync.
* **Encrypted file fallback:** `VSYNC001 | nonce | AES-256-GCM(ciphertext+tag)`, AAD = magic; file key from the QR
  (keep the QR private; the key can be rotated).
* **Cloud:** OpenAI and Google over HTTPS via Chromium's network stack (`net.fetch`).

## Process isolation (desktop)
* Renderer: `contextIsolation`, `sandbox`, no `nodeIntegration`, strict CSP (`script-src 'self'`, `connect-src
  'self'`), navigation and new windows blocked, all permission requests denied, webviews blocked.
* Preload exposes an allow-list of channels; main validates every payload with **Zod** and checks the sender frame.
* The renderer can call only an allow-listed subset of engine methods; methods taking file paths, raw keys or AI tool
  execution are reachable only through main-mediated flows (native dialogs).
* The engine has no network access except the opt-in LAN sync listener; it has no SQL/code execution surface.

## AI safety & privacy
* The model never receives the database. It can call 14 read-only tools; each argument is validated by Pydantic
  (`extra="forbid"`, enums of real metric names, ≤ 400-day ranges, ≤ 2 000 points, 200 KB result cap). No SQL, no code.
* **Consent:** before *every* cloud request the user sees the exact JSON body (credentials excluded) and can cancel.
  Approved payloads are logged (time, provider, model, categories, size, SHA-256) under Privacy.
* Private journal habits are removed from tool schemas and results for cloud providers.
* Answers cite evidence IDs; answers containing numbers without any successful tool call are flagged "unverified".
* ChatGPT sign-in tokens and API keys live in main-process encrypted storage and never reach the renderer.
  Sign in with ChatGPT does not give VITALIS access to ChatGPT conversations, memories or ChatGPT Health records.
* Local models are restricted to loopback URLs.

## OAuth
* System browser + loopback redirect on `127.0.0.1` (RFC 8252), PKCE S256, `state`; OIDC `nonce` and ID-token
  signature/issuer/audience validation (jose + JWKS) for ChatGPT; serialized rotating refresh; revocation on sign-out.
* Google: read-only scopes only; identity check (`users/me/identity`); token revocation on disconnect.

## No telemetry
No analytics, crash reporting, advertising or tracking SDKs in either app. No VITALIS servers exist.

## Licensing notes
* OpenAI's Sign-in with ChatGPT DevKit is under a *Noncommercial License* and its `@siwc/local` package is private/
  unpublished. VITALIS does **not** vendor it; it implements the documented OAuth/Responses protocol directly.
* Samsung Health Data SDK: proprietary; not redistributed; distribution of an app using it requires partnership.

## Reporting
This is a personal project; report issues privately to the repository owner.
