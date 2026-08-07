---
name: generate-node-red-flow
description: Generate deterministic, read-only Node-RED Modbus flow JSON from a canonical map and compiled read plan. Use when the user specifically requests a Node-RED import, reusable flow, or probe-mode flow. Use capture-modbus-sample first when sample acquisition is the goal and no probe request exists.
---

# Generate Node-RED Flow

Generate either a raw probe or a final reviewed flow.

## Workflow

1. Require a canonical map.
2. If a read plan is not supplied, invoke `compile-modbus-read-plan` first.
3. Select `probe` or `final` mode.
4. Run `python3 <skill-dir>/scripts/run.py --map <map.json> --plan <read-plan.json> --mode <mode> --output <directory>`.
5. Inspect the preflight findings and manifest.
6. Import the flow into the pinned Node-RED environment before calling it verified.

Use one physical read per compiled block. Use environment placeholders. Start the generated tab disabled. Include catch, status, queue, and watchdog paths. Use unique IDs across route, unit, area, and offset.

Do not generate write nodes. Final mode must stop when required decoding fields are unresolved.
