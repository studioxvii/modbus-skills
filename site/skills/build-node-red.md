# Build Node-RED

Build deterministic read-only Node-RED Modbus flow JSON from a canonical map and compiled read plan.

## Use this when

The user asks for a Node-RED flow, Modbus flex-getter setup, or Node-RED probe/final capture path.

## What you get back

- `node-red/flow.json` - Import this flow into Node-RED.
- `node-red/README.md` - Start here. It explains the connection settings, the single start button, and the capture file.
- `node-red/manifest.json` and `node-red-result.json` - Normally ignore these. They bind the flow to the exact map and read plan and record any holds.

## Example request

Generate a read-only Node-RED Modbus flow.

## Safety boundary

This skill does not write registers, force coils, broadcast, scan a network, or start unbounded polling. Unresolved engineering fields stay visible.

[View the skill source on GitHub](https://github.com/studioxvii/modbus-skills/blob/main/plugins/modbus-skills/skills/build-node-red/SKILL.md)
