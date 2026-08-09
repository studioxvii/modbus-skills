---
name: build-modscan
description: Build deterministic read-only ModScan setup, poll-plan, point-map, and protocol test-message artifacts.
---

# Build ModScan

Generate documented, version-neutral setup files.

Follow `../../references/interaction-contract.md`.

## Process

1. Read `references/options.md` before using options or making a version claim.
2. Infer ModScan32 or ModScan64 from the request or available installation; ask once
   only when the target genuinely affects the output and cannot be discovered.
3. Require a canonical map, read plan, and `probe` or `final` mode.
4. Run `python3 <skill-dir>/scripts/run.py --map <map.json> --plan <read-plan.json> --mode <mode> --output <directory>`.
5. Inspect the setup manifest and generated CSV files.
6. Load them through the named licensed application before marking native verification complete.

## Output files

- `modscan/read-plan.csv` - The bounded read blocks to enter in ModScan.
- `modscan/point-map.csv` - The names and meanings of the returned registers.
- `modscan/test-message-plan.csv` - Optional protocol test messages for verification.
- `modscan/README.md` - Start here for setup instructions.
- The JSON manifest and result files - Normally ignore these. They prove which map and plan produced the CSV files and record any holds.

Completion requires a generated or held status with visible native-verification state.

## Handoff

- No read plan exists: suggest `$plan-reads`.
- The user needs several target formats: suggest `$build-tool-pack`.

Generate documented files only. Leave undocumented native project formats to ModScan.
