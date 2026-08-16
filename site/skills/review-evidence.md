# Review Evidence

Review Modbus source evidence with automated checks and a grouped exception queue, avoiding page-by-page or row-by-row approval.

## Use this when

Source evidence needs status grouping, exception decisions, or confirmation of a bounded extraction scope.

## What you get back

- `report.json` - Open this exception report. It separates verified evidence from the small set of grouped decisions that still need attention. It does not change the source artifact.

## Example request

Show which parsed Modbus fields are confirmed or inferred.

## Safety boundary

This skill does not write registers, force coils, broadcast, scan a network, or start unbounded polling. Unresolved engineering fields stay visible.

[View the skill source on GitHub](https://github.com/studioxvii/modbus-skills/blob/main/plugins/modbus-skills/skills/review-evidence/SKILL.md)
