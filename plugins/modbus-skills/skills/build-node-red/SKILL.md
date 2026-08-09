---
name: build-node-red
description: Build deterministic read-only Node-RED Modbus flow JSON from a canonical map and compiled read plan.
---

# Build Node-RED

Generate a disabled manual-read flow in `probe` or `final` mode.

Follow `../../references/interaction-contract.md`.

## Process

1. Require a canonical map and read plan.
2. Run `python3 <skill-dir>/scripts/run.py --map <map.json> --plan <read-plan.json> --mode <mode> --output <directory>`.
3. Inspect the preflight findings and manifest.
4. Confirm one manual inject and one flex getter exist for each block.
5. Confirm response gates, catch paths, status paths, queues, and watchdogs are present.
6. Import the flow into the pinned Node-RED environment before marking native verification complete.

Completion requires a generated or held flow with no scheduled, deploy-time, or write node.

## Handoff

- No read plan exists: suggest `$plan-reads`.
- A probe returns raw words: suggest `$check-byte-order`.
- The user needs more target formats: suggest `$build-tool-pack`.

Run one manual inject at a time. Final mode decodes confirmed layouts only.
