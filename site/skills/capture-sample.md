# Capture Sample

Create a bounded read-only probe that collects one raw Modbus sample through a selected target tool.

## Use this when

The user needs one physical read, raw words for byte-order work, or a manual probe before decoding.

## What you get back

- `README.md` and the selected tool folder - Start here. These files describe and perform one bounded manual read; they do not connect automatically.
- `tool-pack.zip` - The portable copy of the probe files.
- `manifest.json`, `checksums.sha256`, and `tool-pack-result.json` - Normally ignore these. They verify the probe contents and safety limits.

## Example request

Create a raw read probe so I can determine byte order.

## Safety boundary

This skill does not write registers, force coils, broadcast, scan a network, or start unbounded polling. Unresolved engineering fields stay visible.

[View Capture Sample source on GitHub](https://github.com/studioxvii/modbus-skills/blob/main/plugins/modbus-skills/skills/capture-sample/SKILL.md)
