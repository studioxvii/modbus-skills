---
name: compare-modbus-maps
description: Compare two Modbus register maps using composite point identity and report added, removed, moved, and field-level changes. Use for firmware revisions, device variants, vendor-document updates, or project map drift.
---

# Compare Modbus Maps

Compare normalized maps. Preserve evidence from both sides.

## Workflow

1. Normalize both inputs without guessing.
2. Run `python3 <skill-dir>/scripts/run.py --before <old.json> --after <new.json> --output <diff.json>`.
3. Report added, removed, moved, changed, and unresolved points.
4. Report a point as moved when its stable logical identity remains the same but its route, unit identifier, area, or protocol offset changes.
5. Highlight changes to address, area, unit identifier, datatype, width, access, scale, byte order, enum values, and evidence.

Do not key rows by numeric address alone. Do not collapse points from different register areas or units.
