---
name: review-evidence
description: Review Modbus source evidence, rejected rows, inferences, warnings, and holds before a human map decision.
---

# Review Evidence

Turn source evidence into a clear human decision queue.

## Process

1. Read `references/evidence-status.md`.
2. Run `python3 <skill-dir>/scripts/run.py --input <artifact.json> --output <report.json>`.
3. Separate confirmed, inferred, unresolved, and rejected items.
4. Include source location and match method when present.
5. Present one blocking decision at a time.
6. Record each choice as new evidence while preserving the source value.

Completion requires every reviewed item to have a status and every blocking item to have a decision path.

## Handoff

- The user confirms values or exclusions: suggest `$apply-review`.
- The uncertainty is raw-word interpretation: suggest `$check-byte-order`.

Describe fuzzy evidence as fuzzy. Plausibility alone is not confirmation.
