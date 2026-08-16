# Check Map

Check a normalized Modbus map for identity, range, overlap, width, access, function, and byte-order problems.

## Use this when

The user wants deterministic lint/validation findings on a normalized or reviewed register map.

## What you get back

- `validation.json` - Open this report. It lists passed checks and groups problems by root cause. It does not create or modify a map.

## Example request

Find duplicates overlaps and invalid widths in this Modbus map.

## Safety boundary

This skill does not write registers, force coils, broadcast, scan a network, or start unbounded polling. Unresolved engineering fields stay visible.

[View the skill source on GitHub](https://github.com/studioxvii/modbus-skills/blob/main/plugins/modbus-skills/skills/check-map/SKILL.md)
