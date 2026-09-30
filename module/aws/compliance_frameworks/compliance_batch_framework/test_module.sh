#!/usr/bin/env bash
# Terraform fmt / validate every submodule and run the mocked-provider tests; needs terraform >= 1.7 (or TERRAFORM=path).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
tf="${TERRAFORM:-terraform}"
env="${TF_VAR_env:-dev}"
work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT
cp -R "$here/common" "$here/config" "$work/"

"$tf" fmt -check -recursive "$here"
for sub in iam/glue iam/stepfunctions glue stepfunctions; do
  echo "== $sub"
  mkdir -p "$work/$sub"
  cp -R "$here/$sub/." "$work/$sub/"
  cat > "$work/$sub/versions_test.tf" <<'TF'
terraform {
  required_version = ">= 1.7.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}
provider "aws" { region = "us-east-1" }
TF
  (cd "$work/$sub" && "$tf" init -backend=false -input=false >/dev/null && "$tf" validate -no-color | head -1)
  if [[ -d "$work/$sub/tests" ]]; then
    (cd "$work/$sub" && "$tf" test -no-color -var-file="$work/config/$env.tfvars" | tail -1)
  fi
done
