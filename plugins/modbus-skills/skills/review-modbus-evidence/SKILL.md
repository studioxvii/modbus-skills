---
name: review-modbus-evidence
description: Review Modbus parsing evidence, rejected rows, inferred values, assumptions, warnings, and blocking holds. Use before promoting a candidate map, after PDF extraction, or when a user needs to know which register-map fields are confirmed versus inferred.
---

# Review Modbus Evidence

Present the evidence in a form that supports a human decision.

## Workflow

1. Read `references/evidence-status.md`.
2. Run `python3 <skill-dir>/scripts/run.py --input <artifact.json> --output <report.json>`.
3. Show confirmed, inferred, unresolved, and rejected items separately.
4. Include source location and match method when present.
5. Ask for one blocking decision at a time.
6. Record the decision as new evidence. Do not overwrite the source value.

Never describe fuzzy or inferred evidence as exact. Never promote a row only because it looks plausible.
