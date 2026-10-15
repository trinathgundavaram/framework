#!/usr/bin/env bash
# Build the framework wheel from this folder into ../code/wheels (or the folder given as $1).
set -euo pipefail

src="$(cd "$(dirname "$0")" && pwd)"
out="${1:-$src/../code/wheels}"
python="${PYTHON:-python3}"

mkdir -p "$out"
out="$(cd "$out" && pwd)"
rm -f "$out"/*.whl
if [[ -z "${SOURCE_DATE_EPOCH:-}" ]]; then
  SOURCE_DATE_EPOCH="$(git -C "$src" log -1 --format=%ct -- . 2>/dev/null || true)"
fi
export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-$(date +%s)}"
clean() { rm -rf "$src/build" "$src"/*.egg-info; }
trap clean EXIT
clean
"$python" -m pip wheel --quiet --no-deps --wheel-dir "$out" "$src"
echo "built: $(ls "$out")"
