#!/usr/bin/env bash
# Start 4 map-matrix workers + write takeaways when done.
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

python3 "$MATRIX/build_manifest.py"
python3 "$MATRIX/program_ctl.py" init --manifest "$MATRIX/manifest.json"

CONCURRENCY="${PSTACK_MAP_CONCURRENCY:-4}"
log="artifacts/pstack/map-matrix/pool.log"
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] starting $CONCURRENCY workers" | tee "$log"

pids=()
for i in $(seq 1 "$CONCURRENCY"); do
  nohup bash "$MATRIX/run_one_worker.sh" "$i" >>"artifacts/pstack/map-matrix/worker-${i}.log" 2>&1 &
  pids+=($!)
  echo "worker $i pid ${pids[-1]}" | tee -a "$log"
done

# Wait for all workers
for pid in "${pids[@]}"; do
  wait "$pid" || true
done

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] workers done; writing takeaways" | tee -a "$log"
python3 "$MATRIX/write_takeaways.py" | tee -a "$log"
python3 "$MATRIX/program_ctl.py" status | tee -a "$log"
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] map-matrix complete" | tee -a "$log"
