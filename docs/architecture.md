# Architecture

## Design rule

Keep skills focused on one user goal. Keep fragile calculations and file generation in deterministic code.

## Workflow

```text
source-bundle/v1
  -> candidate-map/v1
  -> modbus-map/v1
  -> modbus-map-lint/v1
  -> modbus-map-evidence-review/v1
  -> human confirmation
  -> reviewed modbus-map/v1
  -> modbus-read-plan/v1
  -> modbus-tool-pack/v1 (probe mode)
  -> capture/v1
  -> modbus-byte-order-evidence/v1
  -> human byte-order confirmation
  -> confirmed modbus-map/v1
  -> modbus-tool-pack/v1 (final mode)
  -> capture/v1
  -> modbus-capture-analysis/v1
```

Human review is an explicit gate. It is not a skill that silently promotes evidence. A final tool adapter consumes only a reviewed map and a compiled read plan.

Byte order is not guessed before tool generation. When byte order is unknown, the workflow first generates a read-only `probe` artifact for one selected target. The user runs one physical read. The evaluator then derives all supported byte and word layouts from that same raw sample. After a human confirms the layout, the workflow regenerates the requested Node-RED, Modpoll, ModScan, or combined pack in `final` mode.

Node-RED can derive all four 32-bit candidates inside the disabled probe flow. It uses one read node for each compiled block. The derived branches do not issue more protocol requests. Modpoll and ModScan return raw words for the same evaluator contract.

## Runtime modes

`probe` mode reads raw values. It can run while byte order is unresolved.

`final` mode produces decoded engineering artifacts. It stops when a required field is unresolved.

## Target selection

The tool-pack workflow accepts Node-RED, Modpoll, ModScan, or any non-empty combination. Modpoll has three explicit profiles:

- `witte-desktop`
- `witte-v12-xml`
- `gavinying-cli`

Every selected target records the same canonical-map and read-plan hashes.
