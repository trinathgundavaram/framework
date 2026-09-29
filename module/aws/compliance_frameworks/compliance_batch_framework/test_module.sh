#!/usr/bin/env bash
# Terraform fmt / validate / mocked-provider tests; needs terraform >= 1.7 (or TERRAFORM=path).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
tf="${TERRAFORM:-terraform}"
[[ -d "$here/dist/wheelhouse" ]] || { echo "run ./build_artifacts.sh <framework repository> first" >&2; exit 2; }
work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT
cp -R "$here"/*.tf "$here"/*.tfvars "$here"/stepfunctions "$here"/code "$here"/dist "$here"/tests "$work/"
cat > "$work/versions_test.tf" <<'TF'
terraform {
  required_version = ">= 1.7.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}
provider "aws" { region = "us-east-1" }
TF
"$tf" fmt -check -recursive "$here"
cd "$work"
"$tf" init -backend=false -input=false >/dev/null
"$tf" validate
"$tf" test -var-file=dev.tfvars
