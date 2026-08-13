---
name: build-tool-pack
description: Build any selected combination of Node-RED, Modpoll, and ModScan from one validated map and read plan. Use when the user wants multiple target tools, an undecided target set, or one combined probe/final pack.
license: Apache-2.0
---

# Build Tool Pack

Generate selected targets from one map and one plan.

Follow `../../references/interaction-contract.md`.

## Process

1. Read `references/tool-pack.md`.
2. Require at least one target, a canonical map, and a shared read plan.
3. Validate the map and verify the plan provenance.
4. Use `probe` mode when required byte order is unresolved. Hold final generation.
5. Run `python3 <skill-dir>/scripts/run.py --request <tool-pack-request.json> --output <directory>`.
6. Report each target as `generated`, `held`, `unsupported`, or `verification-failed`.
7. Verify each generated target independently.

## Output files

- `README.md` - Start here. It lists the selected tools and how to use their folders.
- The `node-red/`, `modpoll/`, and/or `modscan/` folders - The files to import or enter in the selected tools.
- `tool-pack.zip` - The same portable files in one archive for sharing.
- `canonical-map.json` and `read-plan.json` - Included so the pack remains understandable on its own.
- `manifest.json`, `checksums.sha256`, and `tool-pack-result.json` - Normally ignore these. They prove the pack is complete, unchanged, and tied to the correct inputs.

Completion requires a checksummed pack that contains every selected target and no unselected target.

## Stop

- Stop when no target is selected or the map fails validation.
- Hold final generation while required decoding fields are unresolved.
- Keep private review notes and source evidence out of the portable pack.

## Handoff

- The map is not reviewed: suggest `review-map`.
- A probe returns raw words: suggest `check-byte-order`.

Final mode requires a plan bound to the exact map hash. Keep private review notes and source evidence out of the portable pack.
