# PDF Evidence Rules

- Record the source hash and physical PDF page index.
- Record the printed page label separately when it is visible.
- Preserve a short source excerpt or reconstructed row.
- Record whether the match is exact, coordinate-derived, OCR-derived, fuzzy, or inferred.
- Keep page selection separate from row confidence.
- Do not give unlabeled fields a passing accuracy score.
- Do not calculate precision from a partial truth set.
- Never merge separately addressed rows because their names look similar.
- Require human review before promotion to a canonical map.

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
6. Require visual source-page review before confirmation.
7. Reject uncertain labels, row boundaries, or column boundaries.

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

The page array must contain each page in the selected contiguous range exactly once. Both `input_hashes.source_pdf` and `source_sha256` must match the input PDF. The command stores only derived rows, short excerpts, tool metadata, and an input digest. It does not copy the full OCR text into the output artifact.

Do not upload the manual. Do not store full OCR text or page images in repository artifacts. Do not redistribute the manual.
