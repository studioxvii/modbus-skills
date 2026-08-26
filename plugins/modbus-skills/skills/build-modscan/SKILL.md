---
name: build-modscan
description: Build deterministic read-only BETA ModScan setup, poll-plan, point-map, and protocol test-message artifacts. Use when the user asks for ModScan files, a ModScan read plan, or ModScan probe/final setup.
license: Apache-2.0
---

# Build ModScan (BETA)

Generate documented, version-neutral setup files. Native application
verification has not been run, so this target is BETA.

Follow `../../references/interaction-contract.md`.

## Process

1. Read `references/options.md` before using options or making a version claim.
2. Infer ModScan32 or ModScan64 from the request or available installation; ask once
   only when the target genuinely affects the output and cannot be discovered.
3. Require a canonical map, read plan, and `probe` or `final` mode.
4. Run `python3 <skill-dir>/scripts/run.py --map <map.json> --plan <read-plan.json> --mode <mode> --output <directory>`.
5. Inspect the setup manifest and generated CSV files.
6. Load them through ModScan when available. Otherwise report native verification
   as unavailable and use the fallback only for one reviewed request.
7. Enter `read-plan.csv` and `test-message-plan.csv` rows directly in ModScan.
   Do not reformat function codes, protocol offsets, or unit IDs.

## Output files

- `modscan/read-plan.csv` - The bounded read blocks to enter in ModScan.
- `modscan/point-map.csv` - The names and meanings of the returned registers.
- `modscan/test-message-plan.csv` - Optional protocol test messages for verification.
- `modscan/README.md` - Start here for setup instructions.
- `modscan/pymodbus-read-once.py` - Optional cross-platform FC01-04 fallback. It
  requires one compiled request, endpoint, port, and matching unit ID.
- The JSON manifest and result files - Normally ignore these. They prove which map and plan produced the CSV files and record any holds.

Completion requires a generated or held status with visible native-verification state.

## Windows lab verification

When native ModScan verification runs in the Windows lab, save the receipt under
`artifacts/windows-lab/<asset-id>/modscan/` (git-ignored). Follow the field list in
`tests/windows-lab/campaign.json` (`receipt_schema`) and the matrix in
`docs/windows-lab-setup.md`. Bootstrap example receipt:
`artifacts/windows-lab/synthetic/receipts/modscan.json`.

## Stop

- Stop without a canonical map, read plan, and probe or final mode.
- Never claim native verification when it was not run.
- Do not invent undocumented ModScan configuration formats.

## Handoff

- No read plan exists: suggest `plan-reads`.
- The user needs several target formats: suggest `build-tool-pack`.

Preserve documented ModScan files. Never claim native verification when it was not run.
