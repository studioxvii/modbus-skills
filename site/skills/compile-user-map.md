# Compile User Map

Compile an OEM Modbus PDF or structured register map plus measurement intent into an organized user map, JSON, CSV, and optional target outputs in one resumable run.

## Use this when

The user wants an organized user map or offline outputs from an OEM source rather than a specialist review chain.

## What you get back

- `output/user-map.md` - The short human-readable map organized by measurement group.
- `output/user-map.csv` - The spreadsheet-ready map for people and common tools.
- `output/user-map.json` - The same map in the complete machine-readable format.

## Example request

Turn this OEM Modbus map into an organized user map for temperatures alarms and status.

## Safety boundary

This skill does not write registers, force coils, broadcast, scan a network, or start unbounded polling. Unresolved engineering fields stay visible.

[View Compile User Map source on GitHub](https://github.com/studioxvii/modbus-skills/blob/main/plugins/modbus-skills/skills/compile-user-map/SKILL.md)
