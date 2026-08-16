# Normalize Map

Normalize Modbus candidates into explicit offsets, areas, units, datatypes, widths, access, and byte-order states.

## Use this when

Candidate rows exist and need canonical engineering fields, holds, and source-preserving normalization.

## What you get back

- `normalized.json` - Use this canonical map for validation and later steps. It keeps source values, resolved engineering fields, warnings, and grouped unresolved holds together.

## Example request

Normalize this mixed-convention Modbus map without guessing.

## Safety boundary

This skill does not write registers, force coils, broadcast, scan a network, or start unbounded polling. Unresolved engineering fields stay visible.

[View the skill source on GitHub](https://github.com/studioxvii/modbus-skills/blob/main/plugins/modbus-skills/skills/normalize-map/SKILL.md)
