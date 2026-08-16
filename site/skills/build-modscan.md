# Build ModScan (BETA)

Build deterministic read-only BETA ModScan setup, poll-plan, point-map, and protocol test-message artifacts.

## Use this when

The user asks for ModScan files, a ModScan read plan, or ModScan probe/final setup.

## What you get back

- `modscan/read-plan.csv` - The bounded read blocks to enter in ModScan.
- `modscan/point-map.csv` - The names and meanings of the returned registers.
- `modscan/test-message-plan.csv` - Optional protocol test messages for verification.

## Example request

Generate a documented ModScan read plan.

## Safety boundary

This skill does not write registers, force coils, broadcast, scan a network, or start unbounded polling. Unresolved engineering fields stay visible.

[View the skill source on GitHub](https://github.com/studioxvii/modbus-skills/blob/main/plugins/modbus-skills/skills/build-modscan/SKILL.md)
