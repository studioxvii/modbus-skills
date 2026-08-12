---
name: remap-addresses
description: Preview and apply a known map-wide conversion between Modbus offsets and Modicon reference notation. Use when the user asks to convert 40001-style references, protocol offsets, or another known address convention across a map.
license: Apache-2.0
---

# Remap Addresses

Preview a known conversion before applying it.

Follow `../../references/interaction-contract.md`.

## Process

1. Require the source and target conventions.
2. Require an explicit register area when notation does not prove it.
3. Run `python3 <skill-dir>/scripts/run.py --input <map.json> --from <convention> --to <convention> --output <preview.json>`.
4. Validate invalid offsets and collisions across the whole map.
5. When the requested conventions are explicit and the preview is collision-free,
   apply the conversion without another confirmation. Ask once only for ambiguity or
   a lossy/colliding result.

## Output files

- `preview.json` - Review this before replacing address notation. It shows every old and new address, unresolved conversion, and collision. The input map remains unchanged.

Completion requires a collision-free, hash-bound preview before map output.

## Handoff

- The converted map needs validation: suggest `check-map`.
- Source conventions remain mixed or unknown: suggest `normalize-map`.

Keep the raw source token. Protocol offset zero is valid; offsets above 65535 are not.
