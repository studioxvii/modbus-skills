# Architecture

## Design rule

Keep skills focused on one user goal. Keep fragile calculations and file generation in deterministic code.

All skills use explicit user invocation. This reduces unrelated instructions in the agent context. `$modbus-help` is the only router. It uses the shared user-path guide, recommends the first missing step, and reads the selected skill before it describes that skill.

Operational skills give one to three relevant handoffs after completion. They do not repeat the complete catalog. The shared [`user-paths.md`](../plugins/modbus-skills/references/user-paths.md) file is the source for high-level routing. The workflow catalog remains the machine-readable source for detailed chains and gates.

## Workflow

```text
source-bundle/v1
  -> candidate-map/v1
  -> modbus-map/v1
  -> modbus-map-lint/v1
  -> modbus-map-evidence-review/v1
  -> human decision record (modbus-review-decisions/v1)
  -> apply-review
  -> reviewed modbus-map/v1
  -> modbus-read-plan/v1
  -> modbus-tool-pack/v1 (probe mode)
  -> capture/v1
  -> modbus-byte-order-evidence/v1
  -> human byte-order decision record (modbus-review-decisions/v1)
  -> apply-review
  -> confirmed modbus-map/v1
  -> rebuilt modbus-read-plan/v1
  -> modbus-tool-pack/v1 (final mode)
  -> capture/v1
  -> modbus-capture-analysis/v1
```

Human review is an explicit gate. It is not a skill that silently promotes evidence. The human produces a `modbus-review-decisions/v1` record. `apply-review` applies only its whitelisted, evidence-backed changes to a new map. A final tool adapter consumes only a reviewed map and a compiled read plan.

Byte order is not guessed before tool generation. When byte order is unknown, the workflow first generates a read-only `probe` artifact for one selected target. The user runs one physical read. The evaluator then derives all supported byte and word layouts from that same raw sample. The capture must identify the point, route, unit, area, and protocol offset. Evidence does not select a layout. After a human records the layout decision, the workflow applies it, rebuilds the read plan, and regenerates the requested Node-RED, Modpoll, ModScan, or combined pack in `final` mode.

Node-RED can derive all four 32-bit candidates inside the disabled probe flow. It uses one manual inject and one one-shot getter for each compiled block. The final flow uses the same manual one-shot trigger. Neither flow schedules a read at deploy time. The derived branches do not issue more protocol requests. Modpoll and ModScan return raw words for the same evaluator contract.

## Runtime modes

`probe` mode reads raw values. It can run while byte order is unresolved.

`final` mode produces decoded engineering artifacts. It stops when a required field is unresolved or the plan does not contain the hash of the exact current map.

## Target selection

The tool-pack workflow accepts Node-RED, Modpoll, ModScan, or any non-empty combination. Modpoll has three explicit profiles:

- `witte-desktop`
- `witte-v12-xml`
- `gavinying-cli`

Every selected target records the same source-map and read-plan hashes. A tool pack includes allowlisted runtime-map and read-plan projections. It does not include review reasons, reviewers, source evidence, arbitrary plan metadata, or local source metadata.
