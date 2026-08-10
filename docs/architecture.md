# Architecture

## Design rule

Keep skills focused on one user goal. Keep fragile calculations and file generation in deterministic code.

The Codex and Claude distributions use explicit user invocation. This reduces unrelated
instructions in the agent context. `modbus-help` is the only router. It uses the shared user-path guide,
recommends the largest safe next step, and reads the selected skill before it describes
that skill. Every skill follows the shared fast interaction contract: automate safe
deterministic work, group exceptions, and avoid serial approval loops.

## Distribution adapters

`plugins/modbus-skills` is the canonical source for every skill, shared reference,
runtime module, and helper script. `scripts/build_plugin_variants.py` derives three
packages without copying generated files back into source control:

- Agent Plugins 1.0 uses a root `plugin.json` and unchanged host-neutral skills.
- Codex preserves `.codex-plugin/plugin.json` and each skill's
  `agents/openai.yaml`, including `allow_implicit_invocation: false`.
- Claude uses `.claude-plugin/plugin.json`, omits Codex agent metadata, and adds
  `disable-model-invocation: true` to generated skill frontmatter.

The Claude field is a packaging transform, not canonical content. The portable package
does not invent a cross-client invocation policy where the standard has none. Variant
validation compares the complete file sets and bytes, removes the one documented
Claude adapter before comparing skill content, rejects host-specific tokens in shared
Markdown, and verifies identical Apache license and notice files.

Operational skills give one to three relevant handoffs after completion. They do not repeat the complete catalog. The shared [`user-paths.md`](../plugins/modbus-skills/references/user-paths.md) file is the source for high-level routing. The workflow catalog remains the machine-readable source for detailed chains and gates.

`compile-user-map` is the primary OEM-source outcome. Its deterministic runtime accepts
a supported local source or an existing `modbus-oem-map/v1`, plus a typed selection and
optional targets. It keeps source semantics, user selection, deployment binding, and
legacy target projections separate. Clean offline work completes in one invocation;
only a material source, selection, binding, physical-read, or byte-order exception can
pause the persisted case.

PDF intake uses one shared extraction ladder for both the focused extraction skill and
the outcome compiler: bounded `pdftotext` layout, bounded coordinate text, then
`pdfplumber` table-grid recovery. Oversized manuals use bounded 256-page discovery
chunks instead of a manual page-selection loop. The source contract separates stable
page/table/row identity from register identity and records coverage explicitly.
`offline-complete` requires complete coverage plus no blocking hold on a selected
point.

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
