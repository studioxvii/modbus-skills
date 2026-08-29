#!/usr/bin/env bash
# Loop map-matrix eval → improve imperfect → merge green PRs until 100% clear.
#
# Pass bar: pass_mode=all_evaluable (every weighted skill criterion, no crash).
# N/A skills (captures, live byte-order, etc.) stay excluded via evals.json.
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
LOG="artifacts/pstack/map-matrix/until-clear.log"

log() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$LOG"; }

MAX_ROUNDS="${PSTACK_MAP_MAX_ROUNDS:-}"
if [[ -z "$MAX_ROUNDS" ]]; then
  MAX_ROUNDS=$(python3 -c "import json; print(json.load(open('$MATRIX/evals.json')).get('budgets',{}).get('max_rounds',20))")
fi
IMPROVE_CAP="${PSTACK_MAP_IMPROVE_PER_ROUND:-}"
if [[ -z "$IMPROVE_CAP" ]]; then
  IMPROVE_CAP=$(python3 -c "import json; print(json.load(open('$MATRIX/evals.json')).get('budgets',{}).get('improve_max_per_round',26))")
fi

log "until-clear start max_rounds=$MAX_ROUNDS improve_cap=$IMPROVE_CAP"

python3 "$MATRIX/build_manifest.py"
python3 "$MATRIX/program_ctl.py" init --manifest "$MATRIX/manifest.json"

for round in $(seq 1 "$MAX_ROUNDS"); do
  log "===== ROUND $round / $MAX_ROUNDS ====="
  if [[ "$round" -gt 1 ]]; then
    python3 "$MATRIX/program_ctl.py" requeue-imperfect --round "$round" | tee -a "$LOG"
  fi

  bash "$MATRIX/run_eval_pool.sh"
  python3 "$MATRIX/write_takeaways.py" | tee -a "$LOG"
  python3 "$MATRIX/program_ctl.py" status | tee -a "$LOG"

  if python3 "$MATRIX/program_ctl.py" is-clear >/tmp/pstack-clear.json 2>/dev/null; then
    log "CLEAR — all maps perfect on evaluable skills"
    cp /tmp/pstack-clear.json artifacts/pstack/map-matrix/clear.json
    python3 "$MATRIX/write_takeaways.py" | tee -a "$LOG"
    exit 0
  fi

  mapfile -t imperfect < <(python3 "$MATRIX/program_ctl.py" list-imperfect | python3 -c "import sys,json; print('\n'.join(json.load(sys.stdin).get('imperfect') or []))")
  log "imperfect=${#imperfect[@]}; starting sequential improve (cap=$IMPROVE_CAP)"

  improved=0
  for map_id in "${imperfect[@]}"; do
    if [[ "$improved" -ge "$IMPROVE_CAP" ]]; then
      log "improve cap reached ($IMPROVE_CAP)"
      break
    fi
    receipt="artifacts/pstack/map-matrix/${map_id}/receipt.json"
    if [[ ! -f "$receipt" ]]; then
      log "skip improve $map_id (no receipt)"
      continue
    fi
    log "improve $map_id"
    set +e
    bash "$MATRIX/run_improve.sh" "$map_id" "$receipt" >>"artifacts/pstack/map-matrix/improve-round-${round}.log" 2>&1
    code=$?
    set -e
    if [[ "$code" -eq 0 ]]; then
      improved=$((improved + 1))
      log "improve ok $map_id"
    else
      log "improve failed $map_id (exit $code)"
    fi
  done

  log "merging green improve PRs"
  bash "$MATRIX/merge_green_prs.sh" || true

  # Stay on main with latest merges for next eval round
  git checkout main >/dev/null 2>&1 || true
  git pull --ff-only origin main >/dev/null 2>&1 || true
done

log "until-clear STOPPED after $MAX_ROUNDS rounds without full clear"
python3 "$MATRIX/write_takeaways.py" | tee -a "$LOG"
python3 "$MATRIX/program_ctl.py" status | tee -a "$LOG"
exit 2
