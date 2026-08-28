#!/usr/bin/env bash
# Run one map-matrix worker loop (claim → execute → maybe improve → finish).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
MATRIX="$ROOT/scripts/pstack/map_matrix"
WORKER_ID="${1:?worker id required}"
ENV_FILE="${HOME}/.config/modbus-skills/pstack.env"
# Preserve caller override (run_eval_pool sets PSTACK_AUTO_IMPROVE=0).
AUTO_IMPROVE_OVERRIDE="${PSTACK_AUTO_IMPROVE-}"

# shellcheck disable=SC1091
source "$ROOT/scripts/pstack/no_sudo.sh"
if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
fi
if [[ -n "${AUTO_IMPROVE_OVERRIDE}" ]]; then
  export PSTACK_AUTO_IMPROVE="$AUTO_IMPROVE_OVERRIDE"
fi

cd "$ROOT"
mkdir -p artifacts/pstack/map-matrix
LOG="artifacts/pstack/map-matrix/worker-${WORKER_ID}.log"

log() { printf '[%s][w%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$WORKER_ID" "$*" | tee -a "$LOG"; }

while true; do
  claim=$(python3 "$MATRIX/program_ctl.py" claim --worker-id "$WORKER_ID")
  if echo "$claim" | python3 -c "import sys,json; raise SystemExit(0 if json.load(sys.stdin).get('done') else 1)"; then
    log "queue empty; worker exit"
    exit 0
  fi
  map_id=$(echo "$claim" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
  log "claimed $map_id"

  set +e
  out=$(python3 "$MATRIX/run_worker.py" --map-id "$map_id" 2>&1)
  code=$?
  set -e
  echo "$out" | tee -a "$LOG"
  receipt="artifacts/pstack/map-matrix/${map_id}/receipt.json"
  grade=$(python3 -c "import json; print(json.load(open('$receipt'))['score']['grade'])" 2>/dev/null || echo fail)
  points=$(python3 -c "import json; s=json.load(open('$receipt'))['score']; print(f\"{s['points']}/{s['max_points']}\")" 2>/dev/null || echo "?")

  if [[ "$code" -eq 0 && "$grade" == "pass" ]]; then
    python3 "$MATRIX/program_ctl.py" finish --map-id "$map_id" --status passed --receipt "$receipt" --grade "$grade" --points "$points"
    log "passed $map_id ($points)"
    continue
  fi

  pr_url=""
  if [[ "${PSTACK_AUTO_IMPROVE:-1}" == "1" ]]; then
    log "failed $map_id ($points); improve"
    set +e
    bash "$MATRIX/run_improve.sh" "$map_id" "$receipt" 2>&1 | tee -a "$LOG"
    improve_code=${PIPESTATUS[0]}
    set -e
    if [[ "$improve_code" -eq 0 ]]; then
      pr_url=$(gh pr list --head "pstack/map-${map_id}-$(date -u +%Y%m%d)" --json url -q '.[0].url' 2>/dev/null || true)
      # re-run worker after improve
      set +e
      out=$(python3 "$MATRIX/run_worker.py" --map-id "$map_id" 2>&1)
      code=$?
      set -e
      echo "$out" | tee -a "$LOG"
      grade=$(python3 -c "import json; print(json.load(open('$receipt'))['score']['grade'])" 2>/dev/null || echo fail)
      points=$(python3 -c "import json; s=json.load(open('$receipt'))['score']; print(f\"{s['points']}/{s['max_points']}\")" 2>/dev/null || echo "?")
      if [[ "$code" -eq 0 && "$grade" == "pass" ]]; then
        python3 "$MATRIX/program_ctl.py" finish --map-id "$map_id" --status pr_opened --receipt "$receipt" --grade "$grade" --points "$points" --pr-url "$pr_url"
        log "improved+passed $map_id PR=$pr_url"
        continue
      fi
    fi
  else
    log "failed $map_id ($points); defer improve to until-clear phase"
  fi

  python3 "$MATRIX/program_ctl.py" finish --map-id "$map_id" --status failed --receipt "$receipt" --grade "$grade" --points "$points" --pr-url "$pr_url"
  log "left failed $map_id"
done
