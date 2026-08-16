# Apply Review

Apply a batch of explicit Modbus map decisions while preserving evidence, exclusions, holds, and audit history.

## Use this when

The user confirms layout, exclusion, or field decisions and wants them applied to a new reviewed map.

## What you get back

- `reviewed.json` - Use this new reviewed map. It contains the accepted changes, exclusions, remaining holds, and the evidence trail. The original draft is unchanged.

## Example request

Record my confirmed Modbus map decisions.

## Safety boundary

This skill does not write registers, force coils, broadcast, scan a network, or start unbounded polling. Unresolved engineering fields stay visible.

[View the skill source on GitHub](https://github.com/studioxvii/modbus-skills/blob/main/plugins/modbus-skills/skills/apply-review/SKILL.md)
