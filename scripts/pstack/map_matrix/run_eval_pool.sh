#!/usr/bin/env bash
# Eval-only worker pool (no improve). Used by run_until_clear.sh.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
MATRIX="$ROOT/scripts/pstack/map_matrix"
ENV_FILE="${HOME}/.config/modbus-skills/pstack.env"

# shellcheck disable=SC1091
source "$ROOT/scripts/pstack/no_sudo.sh"
if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
fi

cd "$ROOT"
mkdir -p artifacts/pstack/map-matrix

CONCURRENCY="${PSTACK_MAP_CONCURRENCY:-4}"
export PSTACK_AUTO_IMPROVE=0
log="artifacts/pstack/map-matrix/pool.log"
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] eval pool starting $CONCURRENCY workers (improve=off)" | tee -a "$log"

pids=()
for i in $(seq 1 "$CONCURRENCY"); do
  nohup bash "$MATRIX/run_one_worker.sh" "$i" >>"artifacts/pstack/map-matrix/worker-${i}.log" 2>&1 &
  pids+=($!)
  echo "worker $i pid ${pids[-1]}" | tee -a "$log"
done

for pid in "${pids[@]}"; do
  wait "$pid" || true
done

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] eval pool done" | tee -a "$log"
