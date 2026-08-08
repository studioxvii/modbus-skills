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

In both modes, generate one manual `inject` node and one
`modbus-flex-getter` for each block. Set `repeat` empty and `once` false. Do
not generate `modbus-read` nodes, scheduled polling, or a poll-interval
environment value. In `final` mode, decode only confirmed layouts.

Start each watchdog from the manual inject. Reset it from the successful response. Do not start a new watchdog interval from a successful one-shot response. In `final` mode, require a plan bound to the exact map hash.

Tell the user to run one inject at a time and wait for the response or watchdog result before the next read.

Do not generate write nodes. Final mode must stop when required decoding fields are unresolved.
