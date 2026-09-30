#!/usr/bin/env bash
# Terraform fmt / validate / mocked-provider tests of the AWS module; needs terraform >= 1.7 (or TERRAFORM=path).
set -euo pipefail
repo="$(cd "$(dirname "$0")/.." && pwd)"
module="$repo/module/aws/compliance_frameworks/compliance_batch_framework"
tf="${TERRAFORM:-terraform}"
env="${TF_VAR_env:-dev}"
work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT
cp -R "$module/code" "$module/config" "$work/"

"$tf" fmt -check -recursive "$module"
"$tf" fmt -check "$repo/tests/terraform"
for sub in glue stepfunctions; do
  echo "== $sub"
  mkdir -p "$work/$sub/tests"
  cp -R "$module/$sub/." "$work/$sub/"
  cp "$repo/tests/terraform/$sub.tftest.hcl" "$work/$sub/tests/"
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
  (cd "$work/$sub" && "$tf" test -no-color -var-file="$work/config/$env.tfvars" | tail -1)
done
