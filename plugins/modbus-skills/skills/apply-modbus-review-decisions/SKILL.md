---
name: apply-modbus-review-decisions
description: Apply explicit human review decisions to a normalized Modbus map, preserve the decision evidence, exclude out-of-scope points, and approve the map only when no blocking hold remains. Use after evidence review or byte-order evaluation when a user confirms values or dispositions.
---

# Apply Modbus Review Decisions

Record human choices without changing the source artifact.

## Workflow

1. Read `references/decision-contract.md`.
2. Present one blocking decision at a time.
3. Require a reason and evidence reference for each value or exclusion.
4. For byte-order decisions, include the exact evidence file with `--evidence <byte-order-evidence.json>`.
5. Run `python3 <skill-dir>/scripts/run.py --map <draft.json> --decisions <decisions.json> [--evidence <evidence.json>] --output <reviewed.json>`.
6. Review remaining holds and excluded points.
7. Rebuild the read plan after any map change.

Do not approve a map with blocking holds. Do not reuse a read plan from the pre-decision map. Do not convert a write-only point into a read point.
