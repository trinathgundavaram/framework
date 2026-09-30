#!/usr/bin/env bash
# Build the framework wheel into the Glue module's code/wheels folder.
set -euo pipefail

repo="$(cd "$(dirname "$0")/.." && pwd)"
out="$repo/module/aws/compliance_frameworks/compliance_batch_framework/code/wheels"
python="${PYTHON:-python3}"

mkdir -p "$out"
rm -f "$out"/*.whl
"$python" -m pip wheel --quiet --no-deps --wheel-dir "$out" "$repo"
echo "built: $(ls "$out")"
