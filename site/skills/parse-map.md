# Parse Map

Parse structured Modbus register maps into candidate rows with source values, rejected rows, assumptions, and holds.

## Use this when

The source is CSV, JSON, XML, XLSX, or structured text and needs traceable candidate rows.

## What you get back

- The requested output JSON - Use this candidate map as the input to normalization. It preserves source values and lists rejected rows and parse warnings. It is not yet an approved map.

## Example request

Parse this CSV Modbus register map with rejected-row evidence.

## Safety boundary

This skill does not write registers, force coils, broadcast, scan a network, or start unbounded polling. Unresolved engineering fields stay visible.

[View the skill source on GitHub](https://github.com/studioxvii/modbus-skills/blob/main/plugins/modbus-skills/skills/parse-map/SKILL.md)
