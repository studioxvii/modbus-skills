---
name: generate-modscan-config
description: Generate deterministic, documented, read-only ModScan setup and poll-plan artifacts from a canonical map and read plan. Use when the user specifically requests a ModScan setup, probe, or reusable read plan.
---

# Generate ModScan Config

Generate documented text and CSV artifacts only.

## Workflow

1. Ask whether native verification will use ModScan32 or ModScan64. Generation remains version-neutral.
2. Read `references/options.md` before using `--options` or making a version claim.
3. Require a canonical map and `probe` or `final` mode.
4. If a read plan is not supplied, invoke `compile-modbus-read-plan` first.
5. Run `python3 <skill-dir>/scripts/run.py --map <map.json> --plan <read-plan.json> --mode <mode> --output <directory>`.
6. Inspect the setup manifest.
7. Load the output through the named licensed application before calling it verified.

In `final` mode, use only a plan whose `input_hashes.canonical_map` value matches the exact map. Recompile the plan after any review decision.

Do not invent undocumented `.tst` or `.cfg` binary formats. Do not claim native verification until the generated plan is checked in the named installed application. Do not generate writes, discovery scans, or unbounded polling.
