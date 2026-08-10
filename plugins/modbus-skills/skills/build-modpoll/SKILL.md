---
name: build-modpoll
description: Build deterministic read-only artifacts for gavinying/modpoll or supported Witte Modbus Poll profiles.
license: Apache-2.0
---

# Build Modpoll

Generate artifacts for one explicit Modpoll product profile.

Follow `../../references/interaction-contract.md`.

## Process

1. Read `references/profiles.md` and select `gavinying-cli`, `witte-desktop`, or `witte-v12-xml`.
2. Require a canonical map, read plan, profile, and `probe` or `final` mode.
3. Run `python3 <skill-dir>/scripts/run.py --map <map.json> --plan <read-plan.json> --profile <profile> --mode <mode> --output <directory>`.
4. Inspect the setup manifest, decision report, polling limits, and generated files.
5. Verify with the native target when available. Otherwise report native verification
   as unavailable and use the fallback only for one reviewed request.

## Output files

- The profile folder under `modpoll/` - Start here. It contains the CSV, XML, or command files used to configure the selected Modpoll product.
- The profile `README.md` - Short operator instructions for those files.
- `pymodbus-read-once.py` - Optional cross-platform FC01-04 fallback. It requires
  one compiled request, endpoint, port, and matching unit ID.
- `manifest.json` and `modpoll-result.json` - Normally ignore these. They bind the generated files to the exact map and read plan and record any holds.

Completion requires a generated or held status with visible native-verification state.

## Handoff

- No read plan exists: suggest `$plan-reads`.
- The user also needs Node-RED or ModScan: suggest `$build-tool-pack`.

Preserve native files. Never claim native verification when it was not run.
