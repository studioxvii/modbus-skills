---
name: build-tool-pack
description: Build any selected combination of Node-RED, Modpoll, and ModScan from one reviewed map and read plan.
---

# Build Tool Pack

Generate selected targets from one map and one plan.

## Process

1. Read `references/tool-pack.md`.
2. Require at least one target, a canonical map, and a shared read plan.
3. Validate the map and verify the plan provenance.
4. Use `probe` mode when required byte order is unresolved. Hold final generation.
5. Run `python3 <skill-dir>/scripts/run.py --request <tool-pack-request.json> --output <directory>`.
6. Report each target as `generated`, `held`, `unsupported`, or `verification-failed`.
7. Verify each generated target independently.

Completion requires a checksummed pack that contains every selected target and no unselected target.

## Handoff

- The map is not reviewed: suggest `$review-map`.
- A probe returns raw words: suggest `$check-byte-order`.

Final mode requires a plan bound to the exact map hash. Keep private review notes and source evidence out of the portable pack.
