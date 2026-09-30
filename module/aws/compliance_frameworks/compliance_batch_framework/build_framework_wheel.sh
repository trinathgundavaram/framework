#!/usr/bin/env bash
# Usage: ./build_framework_wheel.sh <framework repository>  -> common/wheels/cms_compliance_framework-*.whl
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
src="${1:-}"
if [[ -z "$src" || ! -f "$src/pyproject.toml" ]]; then
  echo "usage: $0 <path to the framework repository (contains pyproject.toml)>" >&2
  exit 2
fi
python="${PYTHON:-python3}"
out="$here/common/wheels"

mkdir -p "$out"
rm -f "$out"/*.whl
"$python" -m pip wheel --quiet --no-deps --wheel-dir "$out" "$(cd "$src" && pwd)"
echo "built: $(ls "$out")"
