# Build Custom Export

Build a deterministic declarative text or CSV export from a documented example and canonical Modbus map.

## Use this when

The user needs a reusable text or CSV exporter and not a Node-RED, Modpoll (BETA), or ModScan (BETA) adapter.

## What you get back

- `rendered-output.txt` - Open or import this generated data file.
- `format-config.json` - Keep this small recipe so the same format can be generated again.
- `evidence.json` - Normally ignore this. It records which example and map produced the output.

## Example request

Define a declarative Modbus CSV format from this example.

## Safety boundary

This skill does not write registers, force coils, broadcast, scan a network, or start unbounded polling. Unresolved engineering fields stay visible.

[View Build Custom Export source on GitHub](https://github.com/studioxvii/modbus-skills/blob/main/plugins/modbus-skills/skills/build-custom-export/SKILL.md)
