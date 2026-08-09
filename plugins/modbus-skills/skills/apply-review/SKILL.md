---
name: apply-review
description: Apply a batch of explicit Modbus map decisions while preserving evidence, exclusions, holds, and audit history.
---

# Apply Review

Apply confirmed decisions to a new map. Preserve the source artifact.

Follow `../../references/interaction-contract.md`.

## Process

1. Read `references/decision-contract.md`.
2. Collect all independent blocking decisions into one compact batch.
3. Require a reason and evidence reference for each distinct value, exclusion, or
   shared-scope disposition; reuse one evidence reference across its full scope.
4. For byte order, require the exact evidence file and matching sample identity.
5. Run `python3 <skill-dir>/scripts/run.py --map <draft.json> --decisions <decisions.json> [--evidence <evidence.json>] --output <reviewed.json>`.
6. Run once for the complete batch and report applied decisions, exclusions, audit
   data, and only the remaining holds.

Completion requires a new map whose approval state matches its remaining holds.

## Handoff

- Holds remain: suggest `$review-evidence`.
- The map changed and is ready: suggest `$plan-reads`.

Keep write-only points out of the read map. Rebuild every stale read plan.
