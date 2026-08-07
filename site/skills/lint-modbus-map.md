# Lint Modbus Map

Validate a normalized Modbus map for duplicate identities, overlaps, invalid ranges, datatype-width conflicts, area and function-code conflicts, unresolved fields, unsafe writes, and scoped byte-order inconsistencies. Use when explicit map fields exist and the goal is deterministic validation only. Use diagnose-modbus-map for end-to-end cleanup of a raw or messy map.

## Common requests

- Find duplicates overlaps and invalid widths in this Modbus map.
- Please find duplicates overlaps and invalid widths in this Modbus map.
- Can you find duplicates overlaps and invalid widths in this Modbus map.
- I need you to find duplicates overlaps and invalid widths in this Modbus map.
- Help me find duplicates overlaps and invalid widths in this Modbus map.

## Try it

```text
Use $lint-modbus-map to check this register map for conflicts and unsafe assumptions.
```

Source: `plugins/modbus-skills/skills/lint-modbus-map/SKILL.md`
