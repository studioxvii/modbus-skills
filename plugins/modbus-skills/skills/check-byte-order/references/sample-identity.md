# Byte-Order Sample Identity

Byte-order evidence applies only to the exact sampled point. Record these fields with every capture:

- `sample_id`
- `point_id`
- `route_id`
- `unit_id`
- `area`
- `protocol_offset`
- timestamp with a timezone
- raw 16-bit words in arrival order

Do not infer an identity from the numeric words. Do not reuse evidence when any identity field differs. If the source has more than one possible point, stop and ask the user which point produced the sample.

The evaluator can show plausible interpretations. It does not choose a layout, update a map, or approve a final artifact. A human must record an explicit byte-order decision with a reason and evidence reference. Apply that record with `apply-review`, then rebuild the read plan.
