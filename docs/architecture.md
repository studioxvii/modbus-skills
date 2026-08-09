# Architecture

## Design rule

Keep skills focused on one user goal. Keep fragile calculations and file generation in deterministic code.

All skills use explicit user invocation. This reduces unrelated instructions in the
agent context. `$modbus-help` is the only router. It uses the shared user-path guide,
recommends the largest safe next step, and reads the selected skill before it describes
that skill. Every skill follows the shared fast interaction contract: automate safe
deterministic work, group exceptions, and avoid serial approval loops.

Operational skills give one to three relevant handoffs after completion. They do not repeat the complete catalog. The shared [`user-paths.md`](../plugins/modbus-skills/references/user-paths.md) file is the source for high-level routing. The workflow catalog remains the machine-readable source for detailed chains and gates.

`$compile-user-map` is the primary OEM-source outcome. Its deterministic runtime accepts
a supported local source or an existing `modbus-oem-map/v1`, plus a typed selection and
optional targets. It keeps source semantics, user selection, deployment binding, and
legacy target projections separate. Clean offline work completes in one invocation;
only a material source, selection, binding, physical-read, or byte-order exception can
pause the persisted case.

The compiler stores owner-only, hash-indexed case artifacts and commits the case
manifest last. Exact resume replay is idempotent; stale or broadened decisions are
rejected before mutation. Portable user outputs exclude local paths, transport data,
raw excerpts, and captures. The compiler never opens a device connection.

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

Human judgment is an exception gate, not a mandatory ceremony between deterministic
stages. Clean maps proceed when automated checks pass. When evidence is genuinely
ambiguous, the human produces one batched `modbus-review-decisions/v1` record for all
independent choices. `apply-review` applies only its whitelisted, evidence-backed
changes to a new map. A final tool adapter consumes a validated map and compiled plan.

Byte order is not guessed before tool generation. When byte order is unknown, the workflow first generates a read-only `probe` artifact for one selected target. The user runs one physical read. The evaluator then derives all supported byte and word layouts from that same raw sample. The capture must identify the point, route, unit, area, and protocol offset. Evidence does not select a layout. After a human records the layout decision, the workflow applies it, rebuilds the read plan, and regenerates the requested Node-RED, Modpoll, ModScan, or combined pack in `final` mode.

Node-RED can derive all four 32-bit candidates inside the disabled probe flow. It uses one manual inject and one one-shot getter for each compiled block. The final flow uses the same manual one-shot trigger. Neither flow schedules a read at deploy time. The derived branches do not issue more protocol requests. Modpoll and ModScan return raw words for the same evaluator contract.

## Runtime modes

`probe` mode reads raw values. It can run while byte order is unresolved.

`final` mode produces decoded engineering artifacts. It stops when a required field is unresolved or the plan does not contain the hash of the exact current map.

Sparse selected points share a read only inside one explicit readable island whose
route, unit, area, function, inclusive range, reason, and evidence are hash-bound to the
plan. Unknown, reserved, or hazardous gaps split requests. Every bridged unselected
interval remains visible in the request trace.

## Target selection

The tool-pack workflow accepts Node-RED, Modpoll, ModScan, or any non-empty combination. Modpoll has three explicit profiles:

- `witte-desktop`
- `witte-v12-xml`
- `gavinying-cli`

Every selected target records the same source-map and read-plan hashes. A tool pack includes allowlisted runtime-map and read-plan projections. It does not include review reasons, reviewers, source evidence, arbitrary plan metadata, or local source metadata.
