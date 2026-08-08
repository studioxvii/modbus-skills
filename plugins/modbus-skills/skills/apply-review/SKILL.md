---
name: apply-review
description: Apply explicit human decisions to a Modbus map while preserving evidence, exclusions, holds, and audit history.
---

# Apply Review

Apply confirmed decisions to a new map. Preserve the source artifact.

## Process

1. Read `references/decision-contract.md`.
2. Present one blocking decision at a time.
3. Require a reason and evidence reference for each value or exclusion.
4. For byte order, require the exact evidence file and matching sample identity.
5. Run `python3 <skill-dir>/scripts/run.py --map <draft.json> --decisions <decisions.json> [--evidence <evidence.json>] --output <reviewed.json>`.
6. Report applied decisions, exclusions, audit data, and remaining holds.

Completion requires a new map whose approval state matches its remaining holds.

## Handoff

- Holds remain: suggest `$review-evidence`.
- The map changed and is ready: suggest `$plan-reads`.

Keep write-only points out of the read map. Rebuild every stale read plan.
