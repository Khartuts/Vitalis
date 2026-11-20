#!/usr/bin/env bash
# Builds the self-contained Windows engine runtime (no Python install needed on the user's PC):
#   runtime/engine/python/            official CPython for Windows x64 (python.org build, NuGet package "python")
#   runtime/engine/python/Lib/site-packages/   win_amd64 wheels from requirements.lock.txt + vitalis_engine
# Runs on Linux or Windows (Git Bash). Requires: curl, unzip, a host Python 3.12 with pip.
set -euo pipefail
PYVER="${PYVER:-3.12.10}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/runtime/engine"
HOSTPY="${HOSTPY:-python3.12}"
rm -rf "$OUT" && mkdir -p "$OUT"
TMP="$(mktemp -d)"
echo "==> Downloading CPython $PYVER for Windows x64 (NuGet: python)"
curl -fsSL "https://api.nuget.org/v3-flatcontainer/python/$PYVER/python.$PYVER.nupkg" -o "$TMP/python.nupkg"
# Extract tools/ (the interpreter + standard library) with Python's zipfile: identical behaviour on Linux and Windows.
"$HOSTPY" - "$TMP/python.nupkg" "$OUT/python" <<'PYEOF'
import os, sys, zipfile
src, dst = sys.argv[1], sys.argv[2]
n = 0
with zipfile.ZipFile(src) as z:
    for info in z.infolist():
        if not info.filename.startswith("tools/") or info.is_dir():
            continue
        target = os.path.join(dst, *info.filename[len("tools/"):].split("/"))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with z.open(info) as fi, open(target, "wb") as fo:
            fo.write(fi.read())
        n += 1
print(f"extracted {n} files")
PYEOF
for f in python.exe python312.dll Lib/os.py Lib/encodings/__init__.py; do
  [ -f "$OUT/python/$f" ] || { echo "ERROR: $f missing from the bundled runtime"; ls -la "$OUT/python" || true; exit 1; }
done
SITE="$OUT/python/Lib/site-packages"
mkdir -p "$SITE"
echo "==> Installing win_amd64 wheels"
"$HOSTPY" -m pip install --quiet --no-compile --target "$SITE" --platform win_amd64 --python-version 3.12 \
  --implementation cp --abi cp312 --only-binary=:all: -r "$ROOT/services/engine/requirements.lock.txt"
echo "==> Adding vitalis_engine"
cp -r "$ROOT/services/engine/vitalis_engine" "$SITE/vitalis_engine"
find "$OUT" -name "__pycache__" -type d -prune -exec rm -rf {} +
find "$SITE" -maxdepth 2 -type d \( -name tests -o -name testing \) -path "*/pandas/*" -prune -exec rm -rf {} + 2>/dev/null || true
rm -rf "$OUT/python/Lib/test" "$OUT/python/Lib/idlelib" "$OUT/python/Lib/tkinter" "$OUT/python/tcl" 2>/dev/null || true
echo "$PYVER" > "$OUT/PYTHON_VERSION"
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*)
    echo "==> Self-test of the bundled interpreter (Windows)"
    "$OUT/python/python.exe" -I -c "import sys, encodings, numpy, scipy, pandas, sqlcipher3, vitalis_engine; print('bundled', sys.version.split()[0], sys.prefix)"
    ;;
esac
du -sh "$OUT"
echo "Done: $OUT"
