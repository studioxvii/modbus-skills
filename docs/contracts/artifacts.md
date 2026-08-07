# Artifact Contracts

## Common envelope

Every top-level JSON workflow artifact must contain:

- `schema_version`
- `artifact_type`
- `assumptions`
- `findings`
- `holds`

A derived artifact must also contain `input_hashes` when its inputs have stable serialized forms. Empty collections stay present. This makes the stop state and the absence of findings explicit.

Hash canonical JSON with sorted keys and compact separators. Exclude clocks and local paths from deterministic content.

Target-native JSON is exempt from this envelope when adding fields would break
the target format. Examples include a Node-RED flow and target-specific setup
documents. Each exempt file must appear by path and SHA-256 value in the
enveloped `modbus-target-result/v1` artifact. A tool-pack manifest embeds those
target results.

The public control schemas are distinct:

- `modbus-target-manifest/v1` describes files for one target adapter.
- `modbus-target-result/v1` is the CLI result for one target adapter.
- `modbus-tool-pack-manifest/v1` describes the contents of one tool pack.
- `modbus-tool-pack/v1` is the workflow result for one tool pack.

The CLI includes `tool-pack-result.json` inside `tool-pack.zip`. The result
envelope does not list or hash itself. It does not claim a hash for the ZIP that
contains it. The core checksum file covers the core pack files and manifest,
not the result envelope or its containing ZIP. This prevents circular hashes.

## OCR evidence input

Local OCR evidence uses `modbus-ocr-evidence/v1` and the common envelope. The
`source_pdf` input hash and `source_sha256` must both equal the SHA-256 value of
the exact input PDF.

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

## Canonical map

A point contains its raw source address and a normalized address object.

```json
{
  "logical_point_id": "line-voltage-ab",
  "route_id": "rtu-line-a",
  "unit_id": 1,
  "source_address": {
    "raw": "40001",
    "convention": "modicon-reference"
  },
  "area": "holding-register",
  "protocol_offset": 0,
  "datatype": "float32",
  "word_count": 2,
  "byte_order": "ABCD",
  "byte_order_confirmed": true,
  "byte_order_status": "confirmed"
}
```

Use this composite identity:

```text
route_id + unit_id + area + protocol_offset + logical_point_id
```

Do not infer an unknown area, unit identifier, datatype, or address convention.

## Byte-order evidence

One immutable sample contains raw 16-bit words. Each candidate interpretation keeps the same `sample_id`.

The evaluator reports candidates. It does not select a winner.

## Tool pack

A tool pack records:

- Requested targets and target profiles.
- Per-target status.
- Canonical-map hash.
- Read-plan hash.
- Adapter versions.
- File paths and SHA-256 values.
- Assumptions, warnings, and holds.

Allowed status values are `generated`, `held`, `unsupported`, and `verification-failed`.
