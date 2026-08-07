---
name: diagnose-modbus-map
description: Run the complete Modbus register-map workflow from parsing through normalization, linting, evidence review, and human confirmation. Use for raw, imported, or messy maps that need end-to-end cleanup. Use lint-modbus-map instead when an already normalized map only needs deterministic checks.
---

# Diagnose Modbus Map

Chain focused stages through versioned artifacts.

## Workflow

1. Parse the source into `candidate-map/v1`.
2. Normalize explicit fields into a reviewed map draft.
3. Lint the draft.
4. Present rejected rows, assumptions, findings, and holds.
5. Ask for blocking confirmations.
6. Produce `canonical-map/v1` only after holds are resolved.

Run `python3 <skill-dir>/scripts/run.py --input <path> --output <directory>` for the deterministic chain.

Do not hide stage artifacts. Do not call a candidate map canonical. Do not continue to final tool generation while required engineering values remain unresolved.
