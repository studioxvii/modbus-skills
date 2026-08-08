---
name: review-map
description: Review a raw or messy Modbus register map through parsing, normalization, checks, evidence, and human decisions.
---

# Review Map

Turn a source map into a traceable draft and decision queue.

## Process

1. For a PDF source, start with `$extract-pdf-map`.
2. Run `python3 <skill-dir>/scripts/run.py --input <path> --output <directory>`.
3. Inspect the candidate map, `map-draft.json`, lint report, evidence review, rejected rows, assumptions, and holds.
4. Present one blocking engineering decision at a time.
5. Keep unresolved values and excluded rows visible.

Completion requires a schema-valid draft, a complete review report, and a disposition path for every blocking hold. The draft is not approved.

## Handoff

- The user confirms values or exclusions: suggest `$apply-review`.
- The user only wants deterministic checks on an existing normalized map: suggest `$check-map`.

Keep stage artifacts visible. Final target generation starts only from a reviewed map.
