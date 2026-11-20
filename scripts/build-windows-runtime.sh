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
unzip -q "$TMP/python.nupkg" "tools/*" -d "$TMP/pkg"
mv "$TMP/pkg/tools" "$OUT/python"
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
du -sh "$OUT"
echo "Done: $OUT"
