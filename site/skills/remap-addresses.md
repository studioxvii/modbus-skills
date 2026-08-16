# Remap Addresses

Preview and apply a known map-wide conversion between Modbus offsets and Modicon reference notation.

## Use this when

The user asks to convert 40001-style references, protocol offsets, or another known address convention across a map.

## What you get back

- `preview.json` / converted map - Review every old and new address, then use the applied map when the conversion is collision-free. The input map remains unchanged.

## Example request

Convert these 40001 references to protocol offsets.

## Safety boundary

This skill does not write registers, force coils, broadcast, scan a network, or start unbounded polling. Unresolved engineering fields stay visible.

[View Remap Addresses source on GitHub](https://github.com/studioxvii/modbus-skills/blob/main/plugins/modbus-skills/skills/remap-addresses/SKILL.md)
