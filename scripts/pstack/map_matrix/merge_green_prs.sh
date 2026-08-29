#!/usr/bin/env bash
# Merge green open pstack/map improve PRs into main.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT"

log="artifacts/pstack/map-matrix/merge.log"
mkdir -p artifacts/pstack/map-matrix
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] merge green pstack/map PRs" | tee -a "$log"

mapfile -t prs < <(gh pr list --search 'head:pstack/map' --state open --json number,headRefName,url -q '.[] | "\(.number)\t\(.headRefName)\t\(.url)"' 2>/dev/null || true)
if [[ "${#prs[@]}" -eq 0 ]]; then
  echo "no open pstack/map PRs" | tee -a "$log"
  exit 0
fi

merged=0
for row in "${prs[@]}"; do
  num="${row%%$'\t'*}"
  rest="${row#*$'\t'}"
  branch="${rest%%$'\t'*}"
  url="${rest#*$'\t'}"
  echo "checking PR #$num ($branch)" | tee -a "$log"
  # Wait briefly for verify if still pending
  for _ in 1 2 3 4 5 6 7 8; do
    checks=$(gh pr checks "$num" 2>&1 || true)
    if echo "$checks" | rg -q 'verify\s+pass'; then
      break
    fi
    if echo "$checks" | rg -q 'verify\s+fail'; then
      echo "skip #$num verify failed" | tee -a "$log"
      continue 2
    fi
    sleep 15
  done
  if ! gh pr checks "$num" 2>&1 | rg -q 'verify\s+pass'; then
    echo "skip #$num verify not green" | tee -a "$log"
    continue
  fi
  if gh pr merge "$num" --squash --delete-branch 2>&1 | tee -a "$log"; then
    merged=$((merged + 1))
    echo "merged $url" | tee -a "$log"
  else
    echo "merge failed #$num" | tee -a "$log"
  fi
done

git fetch origin main
git checkout main
git pull --ff-only origin main
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] merged=$merged; on main $(git rev-parse --short HEAD)" | tee -a "$log"
