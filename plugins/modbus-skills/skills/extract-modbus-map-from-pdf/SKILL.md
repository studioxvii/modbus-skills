---
name: extract-modbus-map-from-pdf
description: Extract candidate Modbus register rows and page evidence from a PDF manual. Use for device manuals, register-table PDFs, page-range extraction, OCR review, or when the user needs traceable rows rather than an unsupported summary.
---

# Extract Modbus Map from PDF

Treat PDF extraction as evidence collection. Do not publish or redistribute the source manual.

## Workflow

1. Record the source path and SHA-256 value.
2. Record inclusive physical PDF page indexes separately from printed page labels.
3. Read `references/pdf-evidence.md` and select the bounded text path or the local OCR review path.
4. Run `python3 <skill-dir>/scripts/run.py --input <manual.pdf> --pages <page-or-range> --output <directory>` when a page range is known. Omit `--pages` only when whole-file extraction is acceptable.
5. For scanned pages, create a local `modbus-ocr-evidence/v1` file and add `--ocr-evidence <ocr.json>`.
6. Review selected pages, rejected rows, and source excerpts.
7. Keep uncertain rows as candidates.
8. Route the result to `review-modbus-evidence` before `normalize-modbus-map`.

If deterministic text extraction is unavailable or the pages are scanned, keep the OCR review hold and use the bounded local evidence handoff. Do not retain full OCR text or page images in output artifacts. Do not invent missing rows. Do not claim precision from partial labels. Do not redistribute the manual.

Read `references/pdf-evidence.md` before promoting extracted rows.
