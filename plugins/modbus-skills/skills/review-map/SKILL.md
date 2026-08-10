---
name: review-map
description: Review a raw or messy Modbus register map through parsing, normalization, linting, and grouped evidence exceptions when source review itself is the requested outcome.
license: Apache-2.0
---

# Review Map

Turn a source map into a traceable draft and compact exception queue.

Use `compile-user-map` instead when the requested outcome is an organized user map,
JSON, CSV, or target output.

Follow `../../references/interaction-contract.md`.

## Process

1. For a PDF source, start with `extract-pdf-map`.
2. Run `python3 <skill-dir>/scripts/run.py --input <path> --output <directory>`.
3. Run parsing, normalization, linting, and evidence review through without pausing.
4. Group rejected rows, assumptions, and holds by shared cause and present all
   independent blocking choices together.
5. Keep unresolved values and excluded rows visible; never ask for blanket approval
   when automated checks find no exception.

## Output files

- `map-draft.json` - Start here if you need the normalized draft map.
- `review.json` - Open this for the compact evidence status and grouped exceptions.
- `lint.json` - Detailed deterministic validation findings; normally use it only to investigate a problem.
- `parsed.json` - The source-shaped candidate rows; normally use it only to trace a value back to parsing.

Completion requires a schema-valid draft, a complete automated review report, and a
disposition path for every blocking exception. A clean draft is ready without an
extra approval ritual.

## Handoff

- The user confirms values or exclusions: suggest `apply-review`.
- The user only wants deterministic checks on an existing normalized map: suggest `check-map`.

Keep stage artifacts visible. Final target generation starts only from a validated map.
