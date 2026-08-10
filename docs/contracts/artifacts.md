# Artifact Contracts

## Common envelope

Every top-level JSON workflow artifact must contain:

- `schema_version`
- `artifact_type`
- `assumptions`
- `findings`
- `holds`

A derived artifact must also contain `input_hashes` when its inputs have stable serialized forms. Empty collections stay present. This makes the stop state and the absence of findings explicit.

A final target requires `input_hashes.canonical_map` in its read plan. The value must be a valid SHA-256 hash and must match the exact map supplied to the target. A compiled plan also shows its `planning_options`. The options hash must match `input_hashes.planning_options`. Sparse reads require an explicit `readable_islands` entry bound to route, unit, area, read function, inclusive offsets, reason, and evidence references. `unsafe_intervals` always split reads, while `max_quantities` can reduce protocol limits. `max_gap` remains a compatibility field but does not authorize reading an unknown gap. Each bridged range records its exact offsets, island ID, reason, and evidence references. Missing, malformed, and stale hashes stop generation. Probe mode can use an unbound raw plan, but it rejects malformed or mismatched provenance when provenance is present.

Hash canonical JSON with sorted keys and compact separators. Exclude clocks and local paths from deterministic content.

## OEM compiler artifacts

The OEM-to-user-map compiler separates source meaning, user intent, and device
deployment facts. It does not add route or unit placeholders merely to make an
offline map look complete.

- `modbus-oem-map/v1` contains source-backed OEM points. Each point has a unique
  `oem_point_id` and one or more bounded `source_refs`. A paginated reference
  carries page and row indexes; a structured source uses a stable `record_id`.
  Its identity excludes route, unit, endpoint, and transport data. Exact
  duplicate IDs are rejected as duplicates; reuse of an ID for different
  content is rejected as a collision.
  PDF rows use page/table/row geometry for source identity, so two OEM tables may
  safely reuse the same register address. Its `source_coverage` records detected
  and covered pages, detected regions, accepted/rejected/quarantined counts,
  whether discovery completed, and the evidence basis. Each
  `source_field_evidence` item binds a raw PDF header and value to its normalized
  point value and source reference. Artifact validity alone never implies complete
  source coverage. Older v1 artifacts without `covered_pages` remain readable but
  cannot support a new PDF completion claim until regenerated.
- `modbus-user-selection/v1` contains the requested measurements and one
  reasoned disposition per referenced OEM point: `included`, `suggested`, or
  `excluded`. Its `input_hashes.oem_map` must match the exact OEM map.
- `modbus-device-binding/v1` supplies a route, unit identifier, transport kind,
  read constraints, and optional point overrides only when a target needs them.
  Its `input_hashes.oem_map` must match the exact OEM map. A bound target requires
  a non-empty route and a unit identifier from 1 through 247.
- `modbus-user-map/v1` is the portable selected map. Its OEM-map and selection
  hashes must both match, and every delivered point must be included exactly
  once by the selection.
- `modbus-compile-case/v1` is local control data, not a portable deliverable. It
  records the immutable source and request hashes, compiler version, state,
  receipts, active packet, next action, and a case-relative artifact index.

`offline-complete` requires no blocking hold on a selected point. A PDF-backed map
also requires discovered pages to be covered and every populated material field to
match confirmed source evidence. The three user deliverables live in `output/`;
control, provenance, and target files remain outside that folder.

The legacy bound identity remains:

```text
route_id + unit_id + area + protocol_offset + logical_point_id
```

When an OEM point is projected into `modbus-map/v1`, its `oem_point_id` becomes
the `logical_point_id`; the supplied binding contributes route and unit. This is
a deterministic mapping and preserves the existing planner and exporter
identity without placing deployment facts in the OEM artifact.

Portable OEM and user-map artifacts use allowlisted semantic and provenance
data. They reject credentials, endpoints, raw evidence payloads, captures,
source excerpts, and local paths. Source references carry identifiers such as
page, row, region, and hashes; private evidence remains in local case storage.
Case artifact paths must be normalized, case-relative paths and each indexed
artifact carries its schema and SHA-256 value.

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

One immutable sample contains raw 16-bit words. Each candidate interpretation keeps the same `sample_id`. The sample also records `point_id`, `route_id`, `unit_id`, `area`, and `protocol_offset`. A byte-order decision cannot use evidence without this complete identity.

The evaluator reports candidates. It does not select a winner or update a map.

## Review decisions

`modbus-review-decisions/v1` records a human review before a map can change. It contains the exact canonical-map semantic hash, a stable `review_id`, timezone-qualified `reviewed_at`, `reviewer`, `approve_map`, and a decision array. Each decision names a point and either sets one permitted field or excludes that point. Each set decision includes a reason and at least one evidence reference.

A byte-order decision references one supplied `modbus-byte-order-evidence/v1` artifact by semantic SHA-256. The apply command verifies that the evidence sample identity matches the map point and that the selected layout and datatype exist in the candidate set.

`apply-review` creates a new map. It retains an audit record, excluded-point
dispositions, and batch hold dispositions. One hold decision may resolve a shared
bounded source-confirmation scope by exact hold code; it must cite evidence for the
source hash, selection, record count, and exceptions. It approves the result only when
the human requested approval and no blocking hold remains. A map change invalidates the
previous read plan, so the workflow must compile a new plan before final generation.

Evidence-review status is `ready` when automated checks produce no decision groups and
`blocked` when one or more grouped decisions remain. A global source hold produces one
artifact-scoped decision group, not one group per page or record.

## Tool pack

A tool pack records:

- Requested targets and target profiles.
- Per-target status.
- Canonical-map hash.
- Read-plan hash.
- Adapter versions.
- File paths and SHA-256 values.
- Assumptions, warnings, and holds.

The pack file named `canonical-map.json` uses the portable `modbus-runtime-map/v1` schema. It contains an allowlist of runtime point fields and the source-map hash. The pack file named `read-plan.json` is also an allowlisted projection. It contains only request, point-trace, original map-provenance, visible planning-option, and sanitized hold fields. Projection never rebinds a missing, malformed, or stale plan to the supplied map. Both projections exclude review audit records, approval identities, source evidence, and local source metadata. `portable_map_hash` and `portable_read_plan_hash` identify the projected files. `map_hash` and `read_plan_hash` identify the exact reviewed source inputs.

Allowed status values are `generated`, `held`, `unsupported`, and `verification-failed`.
