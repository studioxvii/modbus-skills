---
name: capture-sample
description: Generate a bounded read-only probe pack and stop before the live Modbus read. Use when the user needs an operator-controlled sample, raw words for byte-order work, or a manual probe before decoding; the operator or enabled target tool creates capture.json only after confirmation.
license: Apache-2.0
---

# Capture Sample

Generate an operator-controlled probe pack for one bounded read. This skill does
not connect to the device or perform the read.

Follow `../../references/interaction-contract.md`.

## Process

1. Read `references/probe-request.md`.
2. Require route, unit identifier, register area, protocol offset, word count, and target.
3. Run `python3 <skill-dir>/scripts/run.py --request <probe.json> --output <directory>`.
4. Confirm the probe pack uses only function codes 01 through 04 and permits one
   physical attempt, with no automatic retry after a timeout or failed response.
5. Present the complete probe pack and one scoped live-read confirmation gate,
   then stop. Do not enable the target tool or perform the live Modbus read.
6. After confirmation, the operator or enabled target tool performs the bounded
   read and creates `capture.json`. For Node-RED, use the generated
   `MODBUS_CAPTURE_PATH` output directly. For other tools, save the returned raw
   words as `capture/v1` with the complete sample identity.

## Output files

The skill writes only the probe pack:

- `README.md` and the selected tool folder - Start here. These files contain instructions or operator-controlled artifacts for one bounded read. The skill does not run them.
- `tool-pack.zip` - The portable copy of the probe files.
- `manifest.json`, `checksums.sha256`, and `tool-pack-result.json` - Normally ignore these. They verify the probe contents and safety limits.

`capture.json` is not a skill output. The operator or enabled target tool creates
it only after confirmation and the gated live read.

Completion requires a generated probe pack and one presented live-read gate. This skill
does not capture the sample itself.

## Stop

- Require a unit ID from 1 through 247. Unit ID 0 is forbidden because this package
  does not generate broadcast requests. Modbus TCP gateway unit IDs 0 and 255 are
  not accepted in this release.
- Stop for writes, broadcasts, discovery scans, stored credentials, or unbounded polling.
- Stop if probe identity is incomplete.
- Do not run the live read. Present the probe and wait for the operator.
- Do not generate final decoded engineering values.

## Handoff

- Unknown byte order or datatype interpretation after the operator saves raw words: suggest `check-byte-order`.
- Time-series troubleshooting after a capture exists: suggest `analyze-capture`.

Keep the probe manual, bounded, credential-free, and read-only.
