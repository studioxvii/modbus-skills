---
name: plan-reads
description: Compile reviewed Modbus points into deterministic, bounded read blocks for function codes 01 through 04.
---

# Plan Reads

Group physical reads without losing point traceability.

## Process

1. Require a reviewed canonical map.
2. Run `python3 <skill-dir>/scripts/run.py --input <map.json> --output <read-plan.json> --max-gap <unused-address-count>`.
3. Verify block quantity, gaps, interval, target limits, and point slices.
4. Confirm every point appears in exactly one physical block.
5. Confirm `planning_options` and its hash record the approved gap policy.
6. For final output, confirm the plan's canonical-map hash matches the exact map.

Completion requires every active point to be planned once with explicit route, unit, area, offset, and width.

Use `--max-gap 0` unless the user approves sparse reads. Replan after every reviewed map change.

## Handoff

- One target: suggest `$build-node-red`, `$build-modpoll`, or `$build-modscan` based on the named tool.
- Several targets: suggest `$build-tool-pack`.
