#!/usr/bin/env bash
# Start a project's workflow by hand with the given steps, in order.
set -euo pipefail

usage() {
  echo "usage: $0 <env> <PROJECT|all_projects> STEP[,STEP...] [--run-type R] [--period P] [--table T] [--as-of D] [--metadata-schema S] [--metadata-db DB] [--load-duplicate yes|no] [--dry-run]" >&2
  echo "steps: BATCH_CREATION FILE_CHECK FILE_LOAD FILE_RULES OVERRIDE_DECISIONS BATCH_CLOSE NOTIFY" >&2
  exit 2
}
[[ $# -ge 3 ]] || usage
env="$1" project="$2" steps="$3"; shift 3
prefix="${NAME_PREFIX:-compliance_batch_framework}"
run_type="" period="" table="" as_of="" metadata_schema="" metadata_db="" load_duplicate="" dry_run=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --run-type) run_type="$2"; shift 2 ;;
    --period)   period="$2";   shift 2 ;;
    --table)    table="$2";    shift 2 ;;
    --as-of)    as_of="$2";    shift 2 ;;
    --metadata-schema) metadata_schema="$2"; shift 2 ;;
    --metadata-db)     metadata_db="$2";     shift 2 ;;
    --load-duplicate)  load_duplicate="$2";  shift 2 ;;
    --dry-run)  dry_run=1;     shift ;;
    *) echo "unknown option $1" >&2; usage ;;
  esac
done

input="$(python3 - "$steps" "$run_type" "$period" "$table" "$as_of" "$metadata_schema" "$metadata_db" "$load_duplicate" "${USER:-unknown}" <<'PY'
import json, sys
steps, run_type, period, table, as_of, metadata_schema, metadata_db, load_duplicate, user = sys.argv[1:]
doc = {"steps": [s.strip().upper() for s in steps.split(",") if s.strip()], "requested_by": user}
doc.update({k: v for k, v in {"run_type": run_type, "period": period, "table": table, "as_of": as_of,
                              "metadata_schema": metadata_schema, "metadata_db": metadata_db,
                              "load_duplicate": load_duplicate}.items() if v})
print(json.dumps(doc))
PY
)"
machine="${prefix}_${project}_${env}"
name="manual-$(echo "$steps" | tr ',' '-' | tr -cd 'A-Za-z0-9_-' | cut -c1-40)-$(date -u +%Y%m%dT%H%M%S)-$RANDOM"

echo "workflow: $machine"
echo "input:    $input"
[[ -n "$dry_run" ]] && exit 0

arn="$(aws stepfunctions list-state-machines --query "stateMachines[?name=='${machine}'].stateMachineArn | [0]" --output text)"
if [[ -z "$arn" || "$arn" == "None" ]]; then
  echo "no workflow named $machine (check the environment and the project code)" >&2
  exit 1
fi
execution="$(aws stepfunctions start-execution --state-machine-arn "$arn" --name "$name" --input "$input" \
  --query executionArn --output text)"
region="$(echo "$arn" | cut -d: -f4)"
echo "started:  $execution"
echo "follow:   https://${region}.console.aws.amazon.com/states/home?region=${region}#/v2/executions/details/${execution}"
