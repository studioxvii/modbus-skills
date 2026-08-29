#!/usr/bin/env bash
# Improve one failed map-matrix item: agent fix + human-readable PR.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
MAP_ID="${1:?map id}"
RECEIPT="${2:?receipt path}"
ENV_FILE="${HOME}/.config/modbus-skills/pstack.env"

# shellcheck disable=SC1091
source "$ROOT/scripts/pstack/no_sudo.sh"
if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
fi

cd "$ROOT"
STAMP=$(date -u +%Y%m%d)
BRANCH="pstack/map-${MAP_ID}-${STAMP}"
BASE_BRANCH=main
IMPROVE_LOCK="$ROOT/artifacts/pstack/map-matrix/improve.lock"
mkdir -p "$ROOT/artifacts/pstack/map-matrix"

# Serialize git checkout / PR work across improvers.
exec 9>"$IMPROVE_LOCK"
if ! flock -w 7200 9; then
  echo "could not acquire improve lock" >&2
  exit 1
fi

git fetch origin "$BASE_BRANCH" 2>/dev/null || true
git checkout "$BASE_BRANCH"
git pull --ff-only origin "$BASE_BRANCH" 2>/dev/null || true
if git show-ref --verify --quiet "refs/heads/$BRANCH"; then
  git checkout "$BRANCH"
else
  git checkout -b "$BRANCH"
fi

mkdir -p artifacts/pstack/map-matrix
LOG="artifacts/pstack/map-matrix/improve-${MAP_ID}-${STAMP}.log"
PLAYBOOK="$ROOT/scripts/pstack/map_matrix/IMPROVER.md"

set +e
export PATH="${HOME}/.local/bin:${PATH}"
AGENT_BIN="${PSTACK_AGENT_BIN:-}"
if [[ -z "$AGENT_BIN" ]]; then
  AGENT_BIN="$(command -v agent || true)"
fi
if [[ -z "$AGENT_BIN" && -x "${HOME}/.local/bin/agent" ]]; then
  AGENT_BIN="${HOME}/.local/bin/agent"
fi
if [[ -z "$AGENT_BIN" ]]; then
  echo "agent CLI not found on PATH (exit 127 previously); set PSTACK_AGENT_BIN" >&2
  exit 127
fi

"$AGENT_BIN" --trust --force -p --workspace "$ROOT" --model claude-sonnet-5-thinking-high "$(cat <<EOF
You are the map-matrix IMPROVER for modbus-skills.

Read and follow: $PLAYBOOK

Failed map id: $MAP_ID
Receipt: $ROOT/$RECEIPT

Tasks:
1. Read the receipt. Identify the smallest product bug that caused the fail (parser, normalize, compile, plan, tool-pack).
2. Fix runtime / skill text / tests — never commit private/modbus-maps files.
3. Re-run: python3 scripts/pstack/map_matrix/run_worker.py --map-id $MAP_ID
4. Also run: python3 scripts/validate_skills.py
5. Commit: fix(pstack/map/$MAP_ID): <short why>
6. Push $BRANCH and open a PR with this exact body shape:

## What broke
- One sentence: what failed on this map and why it matters to users.

## What we changed
- Bullet list of concrete product fixes (not process fluff).

## Lesson learned
- One or two blunt takeaways a PM can read in 10 seconds.

## Evidence
- Map id: $MAP_ID
- Receipt: $RECEIPT
- Re-score: must be perfect under pass_mode=all_evaluable (points == max_points)

## Test plan
- [ ] map-matrix worker for $MAP_ID scores perfect
- [ ] validate_skills.py
- [ ] focused unit test if added

Do not merge. No sudo/pkexec/VM. Do not weaken evals. Hold-only compile without user-map points is still a fail.
EOF
)" 2>&1 | tee "$LOG"
code=${PIPESTATUS[0]}
set -e
exit "$code"
