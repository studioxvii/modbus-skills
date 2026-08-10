# PDF Evidence Rules

- Record the source hash and physical PDF page index.
- Record the printed page label separately when it is visible.
- Preserve a short source excerpt or reconstructed row.
- Record whether the match is exact, coordinate-derived, OCR-derived, fuzzy, or inferred.
- Keep page selection separate from row confidence.
- Do not give unlabeled fields a passing accuracy score.
- Do not calculate precision from a partial truth set.
- Give every row a source identity based on page, table, and row geometry. Register
  address alone is not a source identity because separate tables may reuse it.
- Never merge separately addressed rows because their names look similar.
- When residual source risk remains, require one scoped confirmation for the bounded
  exception group after automated checks. Clean strict extraction and coordinate-derived
  fallback rows that pass quality gates do not require approval. Never require page-by-page
  or row-by-row approval.

## Automatic extraction ladder

1. Preflight the exact `pdftotext` version and the `-f`, `-l`, `-layout`,
   `-bbox-layout`, and `-enc` capabilities before extraction.
2. Use an argument array, a 60-second timeout, a bounded page span, and bounded
   captured output. Treat filenames as data, never shell text.
3. Discover likely register pages from headers and register/address signals before
   requesting a page selection. If one-pass output exceeds its bound, scan sequential
   256-page chunks for at most 4,096 pages or 180 seconds.
4. Parse strict `-layout` rows, then independently parse `-bbox-layout` coordinates.
5. When both text interpretations produce no viable rows, automatically recover drawn
   table grids with `pdfplumber`. This is the same shared fallback used by
   `compile-user-map`; never recreate it as a temporary per-manual script.
6. Preserve field claims with the parser ID and physical source locator. A failed
   strict pass is non-blocking when coordinate recovery passes its gates.
7. Auto-resolve formatting-only agreement. Quarantine only conflicts affecting row
   identity, address, area, width, datatype, or access; keep unaffected rows usable.

The artifact records the extractor receipt, discovered pages and table regions,
accepted rows, rejected or quarantined rows, and a `source_coverage` summary. Coverage
is complete when bounded discovery finishes with no rejected or quarantined rows.
Independent-parser agreement is reported as evidence, not turned into a mandatory
approval. Incomplete discovery or actual exceptions produce one grouped source hold;
the skill never asks for page-by-page approval.

## Bounded text extraction

1. Resolve inclusive physical page indexes before extraction.
2. Pass the smallest contiguous page or page range through `--pages`.
3. Process only those pages when a bounded range is known.
4. Keep full extracted text and rendered images outside repository artifacts.
5. Store only the source hash, page index, printed label, method, and a short excerpt.
6. Remove temporary full-page data when the applicable retention policy permits.

The bundled wrapper accepts comma-and-range page syntax, resolves it to one contiguous bounded range, and limits selection to 256 pages. It does not perform OCR. It accepts bounded local OCR evidence through `--ocr-evidence`.

## Scanned-page review

1. Render only the selected pages on the local machine.
2. OCR each page separately with a locally approved tool.
3. Record the OCR tool and version.
4. Preserve row and column association.
5. Mark each derived row as `ocr-derived`.
6. Inspect the bounded range once and focus on detected exceptions, layout changes,
   and a representative sample of stable rows.
7. Reject uncertain labels, row boundaries, or column boundaries.
8. Record one confirmation for the complete source hash, page range, record count, and
   exception list. A page needs a separate decision only when it has a distinct anomaly.

Use this handoff contract:

```json
{
  "schema_version": "modbus-ocr-evidence/v1",
  "artifact_type": "modbus-ocr-evidence",
  "input_hashes": {
    "source_pdf": "<sha256-of-input-pdf>"
  },
  "assumptions": [],
  "findings": [],
  "holds": [],
  "source_sha256": "<sha256-of-input-pdf>",
  "tool": {
    "name": "<local-ocr-tool>",
    "version": "<version>"
  },
  "pages": [
    {
      "page_index": 42,
      "printed_page_label": "A-7",
      "text": "Address  Name  Data Type\n40001  Example  float32"
    }
  ]
}
```

The page array must contain each page in the selected contiguous range exactly once. Both `input_hashes.source_pdf` and `source_sha256` must match the input PDF. External OCR uncertainty remains one grouped, bounded hold. The command stores only derived rows, short excerpts, tool metadata, and an input digest. It does not copy the full OCR text into the output artifact.

Do not upload the manual. Do not store full OCR text or page images in repository artifacts. Do not redistribute the manual.
