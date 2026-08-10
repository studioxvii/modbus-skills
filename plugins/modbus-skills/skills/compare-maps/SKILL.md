---
name: compare-maps
description: Compare two reviewed Modbus maps and report added, removed, moved, changed, and unresolved points.
license: Apache-2.0
---

# Compare Maps

Compare normalized maps while preserving evidence from both sides.

Follow `../../references/interaction-contract.md`.

## Process

1. Normalize both inputs without changing unresolved engineering values.
2. Run `python3 <skill-dir>/scripts/run.py --before <old.json> --after <new.json> --output <diff.json>`.
3. Report added, removed, moved, changed, and unresolved points.
4. Treat route, unit, area, or protocol-offset changes as moves when logical identity remains stable.
5. Highlight address, datatype, width, access, scale, byte-order, enum, and evidence changes.

## Output files

- `diff.json` - Open this comparison. It lists added, removed, moved, changed, and unresolved points without changing either input map.

Completion requires every point from both inputs to have a disposition.

## Handoff

- The new source still needs review: suggest `$review-map`.
- The requested change is only address notation: suggest `$remap-addresses`.

Use composite identity. Numeric address alone is not an identity.
