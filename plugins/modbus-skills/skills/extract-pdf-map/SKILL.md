---
name: extract-pdf-map
description: Extract traceable Modbus register candidates and page evidence from a bounded PDF manual or page range.
---

# Extract PDF Map

Treat PDF extraction as evidence collection.

## Process

1. Read `references/pdf-evidence.md`.
2. Record the source hash, physical page indexes, and printed page labels separately.
3. Run `python3 <skill-dir>/scripts/run.py --input <manual.pdf> --pages <page-or-range> --output <directory>` for a bounded range.
4. For scanned pages, add a local `modbus-ocr-evidence/v1` file with `--ocr-evidence <ocr.json>`.
5. Review extracted rows, rejected rows, source excerpts, and OCR holds.
6. Keep uncertain rows as candidates.

Completion requires traceable candidate rows or an explicit extraction hold for every selected page.

## Handoff

- Candidate rows exist: suggest `$review-evidence`, then `$normalize-map`.
- The user supplies a structured map instead: suggest `$parse-map`.

Keep the source manual local. Output bounded evidence, not full OCR text or page images.
