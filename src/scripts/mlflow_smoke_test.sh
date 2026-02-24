#!/usr/bin/env bash
set -euo pipefail

tracking_uri="${MLFLOW_TRACKING_URI:-}"
if [[ -z "$tracking_uri" ]]; then
  host="${MLFLOW_HOST:-mlflow}"
  port="${MLFLOW_PORT:-5000}"
  tracking_uri="http://${host}:${port}"
fi

experiment_name="${MLFLOW_TEST_EXPERIMENT:-mlflow_smoke_test}"

api_post() {
  local path="$1"
  local data="$2"
  curl -sS -X POST "${tracking_uri}${path}" \
    -H "Content-Type: application/json" \
    -d "$data"
}

api_get() {
  local path="$1"
  shift
  curl -sS -G "${tracking_uri}${path}" "$@"
}

json_get() {
  local key="$1"
  local input="$2"
  if command -v jq >/dev/null 2>&1; then
    printf '%s' "$input" | jq -r "$key"
    return 0
  fi
  python - <<'PY' "$key" "$input"
import json
import sys

key = sys.argv[1]
obj = json.loads(sys.argv[2])

# Minimal jq-like extraction for known fields.
if key == '.experiment.experiment_id':
    value = obj.get('experiment', {}).get('experiment_id')
elif key == '.experiment_id':
    value = obj.get('experiment_id')
elif key == '.run.info.run_id':
    value = obj.get('run', {}).get('info', {}).get('run_id')
elif key == '.run.data.metrics.ok':
    value = obj.get('run', {}).get('data', {}).get('metrics', {}).get('ok')
else:
    value = None

print('' if value is None else value)
PY
}

get_exp_resp="$(api_post /api/2.0/mlflow/experiments/get-by-name "{\"experiment_name\":\"${experiment_name}\"}")"
experiment_id="$(json_get '.experiment.experiment_id' "$get_exp_resp")"

if [[ -z "$experiment_id" ]]; then
  create_exp_resp="$(api_post /api/2.0/mlflow/experiments/create "{\"name\":\"${experiment_name}\"}")"
  experiment_id="$(json_get '.experiment_id' "$create_exp_resp")"
fi

if [[ -z "$experiment_id" ]]; then
  echo "[FAIL] experiment_id not found" >&2
  exit 2
fi

create_run_resp="$(api_post /api/2.0/mlflow/runs/create "{\"experiment_id\":\"${experiment_id}\"}")"
run_id="$(json_get '.run.info.run_id' "$create_run_resp")"

if [[ -z "$run_id" ]]; then
  echo "[FAIL] run_id not found" >&2
  exit 3
fi

ts_ms=$(( $(date +%s) * 1000 ))
api_post /api/2.0/mlflow/runs/log-parameter "{\"run_id\":\"${run_id}\",\"key\":\"source\",\"value\":\"container\"}" >/dev/null
api_post /api/2.0/mlflow/runs/log-metric "{\"run_id\":\"${run_id}\",\"key\":\"ok\",\"value\":1.0,\"timestamp\":${ts_ms},\"step\":0}" >/dev/null

get_run_resp="$(api_get /api/2.0/mlflow/runs/get --data-urlencode "run_id=${run_id}")"
metric_ok="$(json_get '.run.data.metrics.ok' "$get_run_resp")"

if [[ "$metric_ok" != "1.0" && "$metric_ok" != "1" ]]; then
  echo "[FAIL] metric mismatch" >&2
  exit 4
fi

echo "[OK] mlflow tracking read/write succeeded"
echo "tracking_uri=${tracking_uri}"
echo "experiment_id=${experiment_id}"
echo "run_id=${run_id}"
