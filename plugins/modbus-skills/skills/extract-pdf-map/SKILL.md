---
name: extract-pdf-map
description: Extract traceable Modbus register candidates and page evidence from a bounded PDF manual or page range.
---

# Extract PDF Map

Treat PDF extraction as evidence collection.

Follow `../../references/interaction-contract.md`.

## Process

1. Read `references/pdf-evidence.md`.
2. Record the source hash, physical page indexes, and printed page labels separately.
3. Run `python3 <skill-dir>/scripts/run.py --input <manual.pdf> --pages <page-or-range> --output <directory>` for a bounded range.
4. For scanned pages, add a local `modbus-ocr-evidence/v1` file with `--ocr-evidence <ocr.json>`.
5. Automatically check page coverage, record counts, unique addresses, row widths,
   source locations, rejected rows, and cross-page consistency.
6. Keep uncertain rows as candidates and group them by exception type.
7. When source confirmation is still required, present the bounded extraction once
   with its source hash, page range, record count, checks, and exceptions. Never ask
   for page-by-page confirmation; one scoped confirmation covers the full range.

Completion requires traceable candidate rows, a compact automated-check summary, and
explicit holds only for actual exceptions or one batch source-confirmation scope.

## Handoff

- Candidate rows exist: suggest `$review-evidence`, then `$normalize-map`.
- The user supplies a structured map instead: suggest `$parse-map`.

Keep the source manual local. Output bounded evidence, not full OCR text or page images.
