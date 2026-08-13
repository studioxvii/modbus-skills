---
name: capture-sample
description: Create a bounded read-only probe that collects one raw Modbus sample through a selected target tool. Use when the user needs one physical read, raw words for byte-order work, or a manual probe before decoding.
license: Apache-2.0
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
6. For Node-RED, use the generated `MODBUS_CAPTURE_PATH` output directly. For other tools, save the returned raw words as `capture/v1` with the complete sample identity.

## Output files

- `README.md` and the selected tool folder - Start here. These files describe and perform one bounded manual read; they do not connect automatically.
- `tool-pack.zip` - The portable copy of the probe files.
- `manifest.json`, `checksums.sha256`, and `tool-pack-result.json` - Normally ignore these. They verify the probe contents and safety limits.
- `capture.json` - Created after the operator performs the read. It is not produced by this skill.

Completion requires a generated probe pack and one presented live-read gate. This skill
does not capture the sample itself.

## Stop

- Stop for writes, broadcasts, discovery scans, stored credentials, or unbounded polling.
- Stop if probe identity is incomplete.
- Do not run the live read. Present the probe and wait for the operator.
- Do not generate final decoded engineering values.

## Handoff

- Unknown byte order or datatype interpretation after the operator saves raw words: suggest `check-byte-order`.
- Time-series troubleshooting after a capture exists: suggest `analyze-capture`.

Keep the probe manual, bounded, credential-free, and read-only.
