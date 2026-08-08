---
name: capture-modbus-sample
description: Create a bounded read-only probe for collecting raw Modbus words through Node-RED, Modpoll, or ModScan. Use for sample acquisition before byte-order or datatype review. Use a target generator when the request is only to render a named output from an existing canonical map and read plan.
---

# Capture Modbus Sample

Generate a probe artifact. Do not generate a final decoded configuration.

## Workflow

1. Read `references/probe-request.md`.
2. Require route, unit identifier, register area, protocol offset, word count, and chosen target.
3. Record the external gate: one physical read, then stop.
4. Run `python3 <skill-dir>/scripts/run.py --request <probe.json> --output <directory>`.
5. Verify that the artifact uses only function codes 01 through 04.
6. Have the user run the probe against the intended device.
7. Save raw words as `capture/v1`.
8. Route the capture to `evaluate-modbus-byte-order`.

The command generates disabled or operator-controlled artifacts. It does not run
the probe. A Node-RED probe has one manual inject control and one flex getter
per compiled block. It has no scheduled polling. At the external gate, run one
physical read and stop. Do not discover addresses. Do not write. Do not store
credentials or private endpoint values. Do not select a byte-order winner
inside the probe.
