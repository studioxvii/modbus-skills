---
name: extract-pdf-map
description: Extract traceable Modbus register candidates, source coverage, and page evidence from a PDF manual or bounded page range. Use when the source is a PDF register manual and the user wants extraction evidence before normalization or compilation.
license: Apache-2.0
---

# Extract PDF Map

Treat PDF extraction as evidence collection.

Follow `../../references/interaction-contract.md`.

## Process

1. Read `references/pdf-evidence.md`.
2. Select an available Python 3.11+ executable that provides `pdfplumber`. If the
   dependency is unavailable, report it and stop instead of installing software during
   extraction.
3. Record the source hash, physical page indexes, and printed page labels separately.
4. Run `<selected-python> <skill-dir>/scripts/run.py --input <manual.pdf> --output <directory>`. Add `--pages <page-or-range>` only when the user already supplied a bounded range.
5. For scanned pages, add a local `modbus-ocr-evidence/v1` file with `--ocr-evidence <ocr.json>`.
6. Automatically check source coverage, record counts, stable source-row identities,
   source locations, rejected rows, and cross-page consistency.
7. Keep uncertain rows as candidates and group them by exception type.
8. When source confirmation is still required, present the bounded extraction once
   with its source hash, page range, record count, checks, and exceptions. Never ask
   for page-by-page confirmation; one scoped confirmation covers the full range.

## Output files

- `pdf-extraction.json` - Open this only when reviewing extraction. It contains the candidate rows, source locations, automated checks, rejected rows, and grouped exceptions. It does not contain page images or full OCR text.

Completion requires traceable candidate rows, a compact automated-check summary, and
explicit holds only for actual exceptions or one batch source-confirmation scope.

## Stop

- Stop on an unreadable or rights-restricted PDF.
- Stop if `pdfplumber` is missing; report the dependency and do not install it.
- Stop for OCR of an entire unbound manual, writes, broadcasts, or scans.
- Do not copy full page images or complete OCR text into the output.

## Handoff

- The user wants organized user-map outputs from the OEM PDF: suggest `compile-user-map`.
- The user wants end-to-end source-map review: suggest `review-map`.
- Candidate rows exist and extraction evidence is the requested outcome: suggest `review-evidence` only when exception groups remain.
- When the user supplies a structured map instead, route to `parse-map`.

Keep the source manual local. Output bounded evidence, not full OCR text or page images.
