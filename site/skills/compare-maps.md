# Compare Maps

Compare two reviewed Modbus maps and report added, removed, moved, changed, and unresolved points.

## Use this when

The user compares firmware/map revisions or asks what changed between two validated maps.

## What you get back

- `diff.json` - Open this comparison. It lists added, removed, moved, changed, and unresolved points without changing either input map.

## Example request

Compare these two Modbus register maps.

## Safety boundary

This skill does not write registers, force coils, broadcast, scan a network, or start unbounded polling. Unresolved engineering fields stay visible.

[View Compare Maps source on GitHub](https://github.com/studioxvii/modbus-skills/blob/main/plugins/modbus-skills/skills/compare-maps/SKILL.md)
