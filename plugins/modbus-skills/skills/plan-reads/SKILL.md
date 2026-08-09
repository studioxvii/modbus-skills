---
name: plan-reads
description: Compile validated Modbus points into deterministic, bounded read blocks for function codes 01 through 04.
---

# Plan Reads

Group physical reads without losing point traceability.

Follow `../../references/interaction-contract.md`.

## Process

1. Require a canonical map with no blocking identity, address, access, width, or
   final-decoding holds; do not require a separate blanket human approval.
2. Run `python3 <skill-dir>/scripts/run.py --input <map.json> --output <read-plan.json> --max-gap <unused-address-count>`.
3. Verify block quantity, gaps, interval, target limits, and point slices.
4. Verify automatically that every point appears in exactly one physical block.
5. Verify `planning_options` and its hash record the gap policy.
6. For final output, verify the plan's canonical-map hash matches the exact map.

Completion requires every active point to be planned once with explicit route, unit, area, offset, and width.

Use `--max-gap 0` without asking. Ask once only when a nonzero sparse-read policy is
needed. Replan automatically after every map change.

## Handoff

- One target: suggest `$build-node-red`, `$build-modpoll`, or `$build-modscan` based on the named tool.
- Several targets: suggest `$build-tool-pack`.
