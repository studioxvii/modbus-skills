---
name: capture-sample
description: Create a bounded read-only probe that collects one raw Modbus sample through a selected target tool.
---

# Capture Sample

Generate an operator-controlled probe for one bounded read.

Follow `../../references/interaction-contract.md`.

## Process

1. Read `references/probe-request.md`.
2. Require route, unit identifier, register area, protocol offset, word count, and target.
3. Run `python3 <skill-dir>/scripts/run.py --request <probe.json> --output <directory>`.
4. Confirm the artifact uses only function codes 01 through 04.
5. Generate and validate the complete probe before asking once for the live physical
   read against the intended device, then stop.
6. Save the returned raw words as `capture/v1` with the complete sample identity.

Completion requires one immutable sample and no generated final decoding.

## Handoff

- Unknown byte order or datatype interpretation: suggest `$check-byte-order`.
- Time-series troubleshooting: suggest `$analyze-capture`.

Keep the probe manual, bounded, credential-free, and read-only.
