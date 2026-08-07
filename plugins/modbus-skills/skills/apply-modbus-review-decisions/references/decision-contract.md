# Review Decision Contract

Use a JSON object with these top-level fields:

- `schema_version`: Exactly `modbus-review-decisions/v1`.
- `canonical_map_hash`: The semantic SHA-256 value of the exact input map.
- `review_id`: A stable review identifier.
- `reviewed_at`: An ISO-8601 timestamp with a timezone.
- `reviewer`: A person or review-role identifier.
- `approve_map`: `true` only when the reviewer intends to approve the result.
- `decisions`: An array of point decisions.

A set decision contains `point_id`, `field`, `value`, `reason`, and at least one `evidence_refs` entry. Supported fields are route, unit, area, protocol offset, datatype, word span, byte order, scale, engineering offset, engineering unit, access, and read function code.

An exclusion decision contains `point_id`, `action: "exclude"`, `reason`, and optional `evidence_refs`. Exclusion removes the point from the active read map and retains it under `excluded_points` with its disposition.

The command creates a new artifact. It never edits the input map. A byte-order decision also sets its confirmation state. The output status is `approved` only when `approve_map` is true and no blocking hold remains.

Do not convert a source-declared write-only point into a readable point. Exclude it from the active read map. An empty active map cannot be approved.

Use the exact JSON field names in this template:

```json
{
  "schema_version": "modbus-review-decisions/v1",
  "canonical_map_hash": "<64 lowercase SHA-256 characters>",
  "review_id": "review-001",
  "reviewed_at": "2026-08-07T12:00:00-04:00",
  "reviewer": "commissioning-engineer",
  "approve_map": true,
  "decisions": [
    {
      "point_id": "runtime",
      "action": "set",
      "field": "byte_order",
      "value": "ABCD",
      "reason": "The known run time is 3600 seconds.",
      "evidence_refs": [
        "sha256:<semantic SHA-256 of the supplied byte-order evidence artifact>"
      ]
    }
  ]
}
```

For a byte-order decision, pass the referenced evidence artifact with `--evidence`. The command verifies its common envelope, semantic hash, sample ID, timestamp, point ID, route, unit, area, protocol offset, datatype, and selected candidate. A free-text reference alone cannot confirm byte order.
