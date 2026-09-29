#!/usr/bin/env bash
# Usage: ./build_artifacts.sh <framework repository>  -> dist/wheelhouse, dist/schema.sql
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
src="${1:-}"
if [[ -z "$src" && -f "$here/../../../../pyproject.toml" ]]; then
  src="$here/../../../.."
fi
if [[ -z "$src" || ! -f "$src/pyproject.toml" ]]; then
  echo "usage: $0 <path to the framework repository (contains pyproject.toml)>" >&2
  exit 2
fi
src="$(cd "$src" && pwd)"
python="${PYTHON:-python3}"
out="$here/dist"

rm -rf "$out"
mkdir -p "$out/wheelhouse"
"$python" -m pip wheel --quiet --no-deps --wheel-dir "$out/wheelhouse" "$src"
framework_wheel="$(ls "$out"/wheelhouse/cms_compliance_framework-*.whl)"
"$python" -m pip download --quiet --dest "$out/wheelhouse" --only-binary=:all: \
  --platform manylinux2014_x86_64 --python-version 3.9 --implementation cp --abi cp39 \
  "$framework_wheel" "typing-extensions>=4.6" pg8000
cp "$src/src/framework/sql/schema.sql" "$out/schema.sql"

echo "built $(ls "$out"/wheelhouse | wc -l | tr -d ' ') wheels in $out/wheelhouse:"
ls -1 "$out/wheelhouse"
