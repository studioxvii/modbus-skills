---
name: build-node-red
description: Build deterministic read-only Node-RED Modbus flow JSON from a canonical map and compiled read plan.
---

# Build Node-RED

Generate a disabled, read-only flow in `probe` or `final` mode.

Follow `../../references/interaction-contract.md`.

## Process

1. Require a canonical map and read plan.
2. Run `python3 <skill-dir>/scripts/run.py --map <map.json> --plan <read-plan.json> --mode <mode> --output <directory>`.
3. Inspect the preflight findings and manifest.
4. Confirm the flow has one trigger and one shared reader per route. Probe mode
   keeps the trigger manual one-shot; final mode uses one bounded five-second
   live-poll trigger.
5. Confirm the sequencer waits for each response or timeout before sending the next request.
6. Confirm the flow writes a complete `capture/v1` file to `MODBUS_CAPTURE_PATH`.
7. Confirm the canvas has a readable primary path, a separate health/failure lane,
   a bounded retry/watchdog lane, labeled groups/comments, and no diagnostic
   wiring crossing the main read path.
8. Confirm the read-only `/modbus-dashboard` HTTP endpoint is present. It may
   render the latest capture rows, but it must not trigger reads, writes, or
   discovery. Only the single final-flow poll trigger may schedule the existing
   sequencer, and it must remain disabled by default.
9. Import the flow into the pinned Node-RED environment before marking native verification complete.

## Output files

- `node-red/flow.json` - Import this flow into Node-RED.
- `node-red/README.md` - Start here. It explains the connection settings, the single start button, and the capture file.
- `node-red/manifest.json` and `node-red-result.json` - Normally ignore these. They bind the flow to the exact map and read plan and record any holds.
- `capture.json` - Created by Node-RED after a run. It contains the raw results and exact point identities for analysis.

The generated flow also exposes a read-only core-HTTP dashboard at
`/modbus-dashboard`. It shows the latest `capture/v1` rows and run state; it
does not add a Modbus trigger or require the optional Node-RED Dashboard plugin.

Completion requires a generated or held flow with no unbounded polling,
deploy-time trigger, or write node. Final-flow polling must preserve one
request in flight.

## Handoff

- No read plan exists: suggest `$plan-reads`.
- A run creates `capture.json`: suggest `$analyze-capture`.
- The analysis finds a supported multi-register layout question: suggest `$check-byte-order`.
- The user needs more target formats: suggest `$build-tool-pack`.

Click the single start button once in probe mode. In final mode, enable the
disabled tab intentionally; its one poll trigger runs the bounded plan every
five seconds while the tab is enabled. Both modes run the plan in order and
keep one request in flight. Final mode decodes confirmed layouts only.
