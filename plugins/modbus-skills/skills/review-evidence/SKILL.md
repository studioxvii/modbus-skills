---
name: review-evidence
description: Review Modbus source evidence with automated checks and a grouped exception queue, avoiding page-by-page or row-by-row approval. Use when source evidence needs status grouping, exception decisions, or confirmation of a bounded extraction scope.
license: Apache-2.0
---

# Review Evidence

Turn source evidence into a compact exception queue.

Follow `../../references/interaction-contract.md`.

## Process

1. Read `references/evidence-status.md`.
2. Run `python3 <skill-dir>/scripts/run.py --input <artifact.json> --output <report.json>`.
3. Run deterministic checks over the full input and separate verified items from
   inferred, unresolved, and rejected exceptions.
4. Group exceptions by code, field, match method, and shared source scope.
5. Present all independent blocking decision groups together. Do not turn a global
   source hold into one decision per page, row, or point.
6. If the user confirms a bounded extraction, record one decision for the complete
   source hash, page range, record count, and exception list while preserving values.

## Output files

- `report.json` - Open this exception report. It separates verified evidence from the small set of grouped decisions that still need attention. It does not change the source artifact.

Completion requires every item to have a status and every exception group to have a
decision path. Verified items require no human response.

## Handoff

- The user confirms values or exclusions: suggest `apply-review`.
- The uncertainty is raw-word interpretation: suggest `check-byte-order`.

Describe fuzzy evidence as fuzzy. Plausibility alone is not confirmation.
