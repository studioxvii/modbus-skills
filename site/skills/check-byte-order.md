# Check Byte Order

Evaluate every supported byte and word layout from one immutable raw Modbus sample without choosing a winner.

## Use this when

Byte order, word order, or multi-register decoding is unknown and raw words or a capture already exist.

## What you get back

- `evidence.json` - Open this candidate table. It says whether word order applies, then shows only layouts supported by the sample width. It records evidence only; it does not change the map.

## Example request

Evaluate ABCD BADC CDAB and DCBA from these words.

## Safety boundary

This skill does not write registers, force coils, broadcast, scan a network, or start unbounded polling. Unresolved engineering fields stay visible.

[View the skill source on GitHub](https://github.com/studioxvii/modbus-skills/blob/main/plugins/modbus-skills/skills/check-byte-order/SKILL.md)
