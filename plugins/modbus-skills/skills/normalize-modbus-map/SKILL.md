---
name: normalize-modbus-map
description: Normalize a candidate Modbus map into explicit protocol offsets, register areas, unit identifiers, datatypes, word counts, access values, and byte-order states. Use after parsing or to represent mixed or unresolved source conventions without a requested map-wide conversion. Use remap-modbus-addresses when the user asks to convert a map between known conventions.
---

# Normalize Modbus Map

Convert only fields that have sufficient source evidence or explicit user confirmation.

## Workflow

1. Run `python3 <skill-dir>/scripts/run.py --input <candidate.json> --output <normalized.json>`.
2. Review every assumption and hold.
3. Ask for one blocking engineering choice at a time.
4. Rerun normalization after confirmation.
5. Route the result to `lint-modbus-map`.

Keep `source_address.raw` separate from `protocol_offset`. Require an explicit area for Modicon references. Permit protocol offset zero. Reject offsets above 65535.

Do not convert an unknown datatype to `uint16`. Do not convert unknown access to read-only. Do not mark byte order as confirmed without evidence.
