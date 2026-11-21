# Android builds of the engine's native Python packages

The phone app runs the same analytics engine as the desktop, on Chaquopy's Python 3.10 for Android (the newest Python
for which Android builds of NumPy, pandas, SciPy, statsmodels and scikit-learn exist; see
`services/engine/requirements.android-compat.txt`). Two packages the engine needs have no published Android build:

| Package | Why the engine needs it | Recipe |
|---|---|---|
| `pydantic_core` 2.50.5 (Rust) | validation of engine calls and AI tool arguments (pydantic 2.14.4) | `recipes/pydantic-core` |
| `sqlcipher3` 0.6.5 (C) | the SQLCipher-encrypted database | `recipes/sqlcipher3` |

They are built by `.github/workflows/android-wheels.yml` with Chaquopy's `build-wheel.py` (pinned commit) for
`arm64-v8a` (phones) and `x86_64` (emulator):

* Python-for-Android comes from Maven Central (`com.chaquo.python:target`), SHA-1 checked against Maven's checksums.
* `pydantic_core` is not an abi3 module, so the job writes a PyO3 config file describing Android CPython 3.10.
* `chaquopy-build-wheel.patch`: Python-for-Android 3.10.22 ships both `libpython3.so` and `libpython3.10.so`; the
  pinned tool maps both to the alias `libpython.so` and fails on the second. The patch keeps the first alias.
* Recipes are copied into the Chaquopy checkout's `packages/` before building: the tool's compiler wrapper drops
  `-I`/`-L` paths outside its own checkout.
* `sqlcipher3` is patched to drop its `conan` build requirement (which would build a desktop OpenSSL); it links the
  OpenSSL `libcrypto` that ships with Chaquopy's Python instead.

The wheels are workflow artifacts (`wheel-<package>-<abi>`); the app build picks them up from there.
