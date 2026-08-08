---
name: remap-addresses
description: Preview and apply a known map-wide conversion between Modbus offsets and Modicon reference notation.
---

# Remap Addresses

Preview a known conversion before applying it.

## Process

1. Require the source and target conventions.
2. Require an explicit register area when notation does not prove it.
3. Run `python3 <skill-dir>/scripts/run.py --input <map.json> --from <convention> --to <convention> --output <preview.json>`.
4. Review invalid offsets and collisions.
5. Apply only after the user confirms the preview.

Completion requires a collision-free preview and explicit confirmation before map output.

## Handoff

- The converted map needs validation: suggest `$check-map`.
- Source conventions remain mixed or unknown: suggest `$normalize-map`.

Keep the raw source token. Protocol offset zero is valid; offsets above 65535 are not.
