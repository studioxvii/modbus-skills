---
name: compile-modbus-read-plan
description: Compile reviewed Modbus points into deterministic, bounded read blocks for function codes 01 through 04 with route, unit, area, gap, interval, and target limits. Use before generating Node-RED, Modpoll, ModScan, or combined tool packs.
---

# Compile Modbus Read Plan

Group physical reads without losing traceability to logical points.

## Workflow

1. Require a reviewed canonical map.
2. Run `python3 <skill-dir>/scripts/run.py --input <map.json> --output <read-plan.json> --max-gap <unused-address-count>`.
3. Inspect block quantity, gaps, polling interval, and target compatibility.
4. Verify every point maps to one physical block and slice.
5. Stop on unresolved area, unit identifier, offset, or width.
6. Confirm `planning_options.max_gap` records the approved sparse-read gap and `input_hashes.planning_options` matches it.
7. For final output, confirm `input_hashes.canonical_map` matches the exact reviewed map.

Use only function codes 01, 02, 03, and 04. Enforce protocol and target quantity limits. Keep sparse-block behavior explicit.

Use `--max-gap 0` unless the user approves sparse reads. For a maximum gap of two unused addresses, use `--max-gap 2`.

Recompile after every reviewed map change. A raw plan without map provenance is suitable for a probe only. Final target generation fails closed when the map hash is missing, malformed, or stale.
