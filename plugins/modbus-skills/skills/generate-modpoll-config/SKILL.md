---
name: generate-modpoll-config
description: Generate deterministic read-only artifacts for Witte Modbus Poll or the open-source gavinying/modpoll tool. Use when the user specifically requests a Modpoll or Modbus Poll artifact, saved poll setup, CLI configuration CSV, or raw probe.
---

# Generate Modpoll Config

Resolve the product profile before generation.

## Profiles

- Read `references/profiles.md` before profile selection.
- Use `gavinying-cli` for the open-source command-line tool.
- Use `witte-desktop` for readable Witte desktop plans and bounded automation.
- Use `witte-v12-xml` for disabled Witte version 12 XML read documents.

## Workflow

1. Require a canonical map, profile, and `probe` or `final` mode.
2. If a read plan is not supplied, invoke `compile-modbus-read-plan` first.
3. Run `python3 <skill-dir>/scripts/run.py --map <map.json> --plan <read-plan.json> --profile <profile> --mode <mode> --output <directory>`.
4. Inspect the generated setup manifest and decision report.
5. Verify with the pinned target implementation.

For Witte, generate documented automation and readable plan files. Let the installed application create native project files. Do not synthesize opaque `.mbp` or `.mbw` data.

Do not generate writes or scans.
