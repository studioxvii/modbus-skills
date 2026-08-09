---
title: Node-RED Live Workflow Campaign - Plan
type: feat
date: 2026-08-09
topic: node-red-live-campaign
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-plan-bootstrap
execution: code
deepened: 2026-08-09
---

# Node-RED Live Workflow Campaign - Plan

**Target repo:** `modbus-skills`

## Goal Capsule

**Objective:** Give a fresh Codex agent a small, human-level way to exercise the real Node-RED target against Generator Fleet Simulator live data at 10 and 50 units.

**Authority:** The Product Contract below, `AGENTS.md`, and the repository's read-only Modbus safety contract govern implementation. The simulator is an external system under test.

**Stop conditions:** Stop before traffic when the target is not local, the simulator is not ready, map/read-plan/flow hashes do not match, the Node-RED runtime or required contrib node is unavailable, or the imported flow contains writes, discovery, deploy-time triggers, or scheduled polling. Stop after one bounded retry on timeout or invalid response.

**Execution profile:** Implement a finite, operator-controlled campaign. Keep raw captures and transcripts under ignored `artifacts/` or `private/` output. A missing native Node-RED runtime is `blocked`/`not-run`, never a pass.

**Tail ownership:** Implementation owns focused contract tests, the native campaign run, sanitized evidence, and the verification-status update. It does not alter the simulator product or claim native acceptance when prerequisites are absent.

## Product Contract

### Summary

The campaign lets Codex generate and import a read-only Node-RED flow, read live simulator registers, compare them with simulator truth, carry a real capture through the existing analysis workflow, and report a bounded 10-unit and 50-unit result.

### Problem Frame

Static flow tests prove deterministic JSON shape but do not prove that a fresh agent can use Node-RED, collect live data, preserve point identity, or explain failures. Modpoll and ModScan are not available on the user's Mac, so Node-RED is the native live-data path for this acceptance work.

### Actors

- **A1 — Codex test agent:** starts or configures the simulator, follows the human-level runbook, imports and operates Node-RED, and produces the run receipt.
- **A2 — Human reviewer:** inspects the shared receipt, capture, analysis, hashes, and terminal status.

### Requirements

- **R1. Fresh-agent journey:** From a task prompt and permitted local inputs, Codex must discover the canonical map/read plan, generate the Node-RED target, preflight its safety, import it while disabled, perform the bounded live run, analyze the capture, and report artifacts, holds, and limits.
- **R2. Two fleet profiles:** Run the same journey against simulator configurations of exactly 10 and exactly 50 units. Cover unit IDs 1 through N and record the reported Modbus port and fleet size from readiness evidence.
- **R3. Read-only target:** Every live run must use only FC01–FC04 read requests. The flow must remain disabled until review, use manual one-shot injects, keep at most one read in flight, and contain no writes, broadcasts, discovery, deploy-time triggers, or scheduled polling. Record one scoped human authorization for the named local campaign before traffic; do not ask for per-read approvals.
- **R4. Provenance and identity:** Bind each run to the canonical-map hash, read-plan hash, flow/manifest hash, simulator configuration, Node-RED and contrib-node versions, run ID, and request budget. Derive the live map from the simulator export at run time or from a permitted local input; do not track that export. Each capture row must retain route, unit, area, protocol offset, timestamp, raw words, derived values, response time, and success/error state.
- **R5. Live correctness:** For a stable simulator snapshot, every selected unit/block must produce exactly one expected response with no missing or duplicate identity, and the returned payload unit ID must match the intended unit rather than the client's default. Raw words must match the simulator register oracle; scaled values must match declared map precision. A timing race is recorded as inconclusive and retried once, not silently accepted.
- **R6. Human workflow analysis:** The live capture must pass through the existing `capture/v1` and `analyze-capture` path. Communication errors remain errors and are not counted as signal values; unresolved identity or byte-order holds remain visible.
- **R7. Finite stress:** After the correctness pass, run three sequential manual rounds at each fleet size with a default one-second cadence, one in-flight read, a 60-second wall-clock cap, and no more than 180 compiled-block reads per profile. The campaign records request count, response latency, queue drain, simulator readiness, and error count.
- **R8. State-change readback:** Trigger one documented simulator-side `fault-and-reset` scenario outside Node-RED, observe the simulator's explicit state/alarm transition within a finite deadline, read the affected unit through Node-RED, and prove the transition is observed before restoration or scenario completion. Node-RED must never write or include command register 20.
- **R9. Recovery:** During one 10-unit run, create one deterministic endpoint outage or simulator restart. The flow must surface a timeout/catch result without emitting a derived stale sample, then either recover on one retry or end `blocked` with partial evidence and cleanup.
- **R10. Honest result:** The report must distinguish `passed`, `failed`, `blocked`, `not-run`, and `inconclusive`. Importing a flow or reaching simulator readiness alone is not a pass.

### Key Flows

- **F1 — Prepare:** Codex verifies prerequisites, configures one fleet profile, waits for readiness, derives or receives the map/read plan, and records hashes before import.
- **F2 — Read and analyze:** Codex imports the disabled flow, performs one bounded read per compiled block for correctness, converts results to `capture/v1`, and runs capture analysis.
- **F3 — Stress and state change:** Codex runs the finite 1-second manual sequence, invokes the simulator-side scenario, and verifies changed live values without changing the flow.
- **F4 — Recover or stop:** Codex handles one timeout/restart, resumes once or records a terminal hold, then stops the scenario, drains/cleans up, and writes the receipt.

### Acceptance Examples

- **AE1. Ten-unit correctness:** With a ready 10-unit simulator and matching hashes, Codex reads units 1–10, produces complete identity coverage, and reports raw/derived values matching the simulator oracle.
- **AE2. Fifty-unit stress:** With a ready 50-unit simulator, the same campaign completes its finite budget without missing or duplicate unit samples, unbounded traffic, or simulator queue/backlog instability.
- **AE3. Scenario readback:** After the simulator-side `fault-and-reset` action, the next bounded Node-RED read shows the expected unit alarm/state transition and later records restoration or a clear blocked state.
- **AE4. Recovery hold:** If the endpoint is unavailable, the receipt contains a timeout communication error, no value sample for the failed request, one retry outcome, and cleanup status.
- **AE5. Safety refusal:** A stale hash, non-loopback endpoint, write/scheduled node, missing required module, or wrong unit response prevents live traffic and yields an actionable blocked result.

### Success Criteria

- A fresh Codex agent can complete the 10-unit journey and the 50-unit journey from the runbook without hidden expected answers.
- Every pass includes a shared receipt, sanitized summary, capture, analyzer output, hashes, versions, request counts, and terminal cleanup state.
- The campaign proves live communication and behavior; static flow assertions remain supporting checks only.
- No tracked file contains simulator exports, private captures, credentials, local absolute paths, or full agent transcripts.

### Scope Boundaries

**In scope:** One Node-RED native campaign, two fleet sizes, live read/capture/analyze, one simulator state-change journey, one recovery journey, and human-reviewable evidence.

**Deferred to follow-up:** 2,000-unit load, long-duration soak, browser dashboard QA, Modpoll/ModScan parity, broad chaos testing, and an automated fleet benchmark dashboard.

**Never:** Modbus writes, broadcasts, discovery scans, public/non-loopback endpoints, credentials, or converting the generated flow to scheduled polling.

### Dependencies

- A pinned Node-RED runtime and compatible `node-red-contrib-modbus` installation on the Mac.
- A clean Generator Fleet Simulator checkout with its documented readiness and scenario controls.
- A reviewed canonical map and read plan whose target points correspond to the simulator export; the source export stays outside tracked repository fixtures.

### Sources

- `docs/testing.md` — human workflow, native acceptance, read-only, and evidence rules.
- `docs/architecture.md` — Node-RED one-shot/manual flow and capture handoff contracts.
- `tests/test_node_red.py` — deterministic flow invariants to reuse, not duplicate as live tests.
- `scripts/run_human_workflow_tests.py` — report shape, sanitized output, and `_node_red_summary` patterns.
- External simulator: `README.md`, `MODBUS_REFERENCE.md`, `/api/ready`, `/api/generators/<unit>/registers`, `/api/metrics`, and the documented scenario controls.

## Planning Contract

### Key Technical Decisions

- **KTD1 — Extend existing workflow evidence:** Add a native acceptance layer beside the current deterministic exporter and human workflow runner. Do not change generated flow semantics or duplicate node-count unit tests.
- **KTD2 — Keep stress outside the flow:** Repeated reads are a finite campaign of manual triggers through the supported local UI or Node-RED control surface. The imported flow remains disabled/manual-only and the existing one-read probe contract stays unchanged; if no safe control surface is available, the run is blocked.
- **KTD3 — Use the simulator as a read oracle:** Configure fleets through the simulator control plane, compare Node-RED raw registers with same-window REST snapshots, and use the simulator control plane for the one scenario. Never use FC06/FC16 from Node-RED.
- **KTD4 — Share one evidence directory:** Store a run manifest, receipts, captures, analyzer output, Node-RED logs, simulator readiness/metrics, and cleanup status under one ignored run ID. Sanitize the report to counts, hashes, statuses, and finding codes.
- **KTD5 — Fail honestly on missing native tooling:** If Node-RED or the contrib module is unavailable, run only contract/preflight checks and mark native acceptance `blocked` or `not-run`; never substitute static JSON proof.
- **KTD6 — Instrument collection outside the exporter:** The generated flow's successful derive output is not a durable capture artifact. Add a test-only sink or equivalent local debug export at import/runtime so the campaign can collect `capture/v1`; do not change the generated exporter or introduce a scheduler/write path.

### High-Level Technical Design

```mermaid
sequenceDiagram
    participant C as Codex
    participant S as Simulator control/API
    participant N as Node-RED flow
    participant A as Capture analyzer
    participant R as Run receipt
    C->>S: Configure 10 or 50 units; wait for readiness
    C->>N: Import disabled flow; verify manifest and endpoint
    loop Finite manual campaign
        C->>N: Trigger one compiled read block
        N->>S: FC01-FC04 read on loopback
        N-->>C: Test-only sink: raw words, derived values, status/timeout
        C->>S: Fetch same-window oracle snapshot
    end
    C->>A: Submit capture/v1 with complete identity
    A-->>R: Communication and signal findings
    C->>S: Run one state-change scenario out-of-band
    C->>N: Read back changed unit; restore/stop scenario
    C->>R: Write hashes, counts, versions, terminal status, cleanup
```

### Assumptions and Deferred Implementation Questions

- The runner may use the locally supported Node-RED control surface discovered during implementation; it must not edit the generated flow to create a scheduler.
- The campaign default is three rounds or 180 compiled-block reads per profile, bounded by 60 seconds; implementation may reduce the budget when the measured request cardinality would exceed the documented one-in-flight rule.
- The simulator-side `fault-and-reset` scenario is the default state-change fixture. If the checked-out simulator exposes a different stable scenario contract, record the replacement in the run manifest without expanding the campaign.

## Implementation Units

### U1. Define the live campaign contract

**Goal:** Give Codex and a human reviewer one concise runbook, profile definition, and evidence contract for the 10/50 campaign.

**Requirements:** R1, R2, R3, R4, R6, R10.

**Dependencies:** None.

**Files:**

- `tests/node_red_live/README.md`
- `tests/node_red_live/agent-task.md`
- `tests/node_red_live/fixtures/campaign.json`
- `tests/test_node_red_live_contract.py`

**Approach:**

1. Define the two fleet profiles, finite budget, readiness gate, loopback requirement, hash bindings, and terminal statuses.
2. Describe the Codex task as a realistic engineering job with expected artifacts and safety stop conditions.
3. Define sanitized report fields and the `capture/v1` row identity without tracking simulator source exports.

**Execution note:** Start with the human prompt and report contract. The test should score behavior and artifacts, not exact agent prose.

**Patterns to follow:** `docs/testing.md`, `tests/test_oem_workflow_campaign.py`, existing fixture/report conventions, and `.gitignore`'s ignored `artifacts/` and `private/` directories.

**Test scenarios:**

- Happy path: campaign metadata contains exactly 10-unit and 50-unit profiles, a finite budget, and explicit `passed/failed/blocked/not-run/inconclusive` statuses.
- Safety edge: contract rejects a non-loopback endpoint, missing hash, scheduled polling, write node, credential, or absolute local path in sanitized output.
- Evidence edge: contract requires complete point identity, raw words, timestamp, response time, and error state for every capture row.

**Verification:** A reviewer can read the runbook and identify the actor, inputs, finite steps, expected artifacts, stop conditions, and pass criteria without inspecting implementation code.

### U2. Add the simulator and Node-RED live adapter

**Goal:** Execute the bounded native boundary against a configured simulator while preserving the generated flow's read-only contract.

**Requirements:** R2, R3, R4, R5, R7, R8, R9.

**Dependencies:** U1.

**Files:**

- `scripts/run_node_red_live_campaign.py`
- `tests/node_red_live/__init__.py`
- `tests/node_red_live/simulator.py`
- `tests/node_red_live/node_red.py`
- `tests/test_node_red_live_campaign.py`

**Approach:**

1. Preflight the pinned Node-RED/contrib-node runtime, loopback endpoint, simulator readiness, manifest, map/read-plan hashes, and watchdog/request limits.
2. Configure isolated 10- and 50-unit simulator sessions, clear any active scenario, collect readiness/state/metrics, and obtain stable STOPPED REST register snapshots for the oracle.
3. Import the generated flow while disabled and trigger compiled blocks manually with one in-flight read. Record unit, function, address, quantity, raw words, derived values, response time, and status.
4. Attach a test-only sink or documented local debug export to collect successful and failed messages, convert them to `capture/v1`, run `analyze-capture`, and write a sanitized receipt plus private raw evidence.
5. Run the simulator-side state-change fixture and one endpoint outage/restart path. Restore or stop the scenario and clean up on every terminal state.

**Technical design:** The adapter should expose primitive lifecycle operations—preflight, configure, import, attach capture sink, trigger one block, collect, snapshot, analyze, recover, cleanup—so a Codex agent can observe checkpoints and resume or stop honestly. It must reuse the existing Node-RED manifest and summary checks rather than reimplement static flow generation. The simulator oracle should use stable register points such as output kW, breaker, engine RPM, alarm word, and transfer mode; command register 20 is excluded.

**Patterns to follow:** `plugins/modbus-skills/runtime/modbus_skills/node_red.py`, `scripts/run_human_workflow_tests.py::_node_red_summary`, `plugins/modbus-skills/skills/capture-sample/references/probe-request.md`, and the simulator's readiness/startup/scenario endpoints.

**Test scenarios:**

- Integration happy path: configured 10-unit simulator reaches readiness, each unit/block is triggered once, REST and Node-RED raw words match, and the capture analyzes successfully.
- Integration scale path: the same campaign at 50 units produces exact expected unit/block cardinality, no duplicates, no missing identities, and a drained simulator queue before completion.
- State path: `fault-and-reset` changes the expected unit alarm/state in the readback and ends restored or explicitly blocked; no Node-RED write request appears.
- State path: the run observes the simulator's reported transition within a deadline, then confirms the corresponding alarm/breaker/register change through the test-only sink; it does not rely on a fixed sleep or generic state register.
- Failure path: a stale hash, missing contrib node, non-local endpoint, or wrong unit response prevents traffic and records a blocked receipt.
- Recovery path: one controlled outage produces a timeout/catch record without a derived value, one retry either succeeds or ends blocked, and cleanup evidence is present.
- Bounded stress path: the finite one-second sequence stops at the configured cycle/time cap and reports latency, error count, request count, readiness, and queue drain.

**Verification:** Native evidence proves actual Node-RED responses and simulator comparisons. Static flow assertions remain supporting checks. A missing native runtime is reported as blocked/not-run with the unmet prerequisite.

### U3. Add the fresh-agent acceptance and regression gate

**Goal:** Make the campaign runnable as a human-level Codex evaluation and keep its contract stable without adding unrelated checks.

**Requirements:** R1, R5, R6, R7, R9, R10.

**Dependencies:** U1, U2.

**Files:**

- `tests/test_node_red_live_agent.py`
- `tests/node_red_live/expected-report.schema.json`
- `docs/verification-status.md`

**Approach:**

1. Run the documented task prompt with a fresh Codex context and only the permitted simulator/map inputs.
2. Score terminal behavior, artifact presence, identity coverage, live oracle comparison, safety refusals, analysis findings, and cleanup rather than wording.
3. Record native availability and the exact reason when the acceptance gate is not run.

**Execution note:** Execute the native campaign before treating any static regression as a release claim. Keep the evaluation to the two fleet journeys, one state change, and one recovery path.

**Patterns to follow:** Existing blind-forward guidance in `docs/testing.md`, sanitized `CaseResult` reports in `scripts/run_human_workflow_tests.py`, and `docs/verification-status.md`'s separation of static, native, and agent evidence.

**Test scenarios:**

- Fresh-agent path: Codex chooses the shortest safe chain, imports the generated flow, completes 10 and 50 profiles, and names the shared evidence artifacts and remaining limits.
- Agent refusal path: Codex refuses to proceed on stale hashes, non-loopback targets, missing runtime, writes, schedules, discovery, or unbounded polling.
- Analysis path: Codex preserves communication errors as errors, keeps unresolved byte-order/identity holds visible, and does not claim a winner from ambiguous evidence.
- Resume path: after a 10-unit failure, the receipt records the checkpoint and the 50-unit result is not falsely marked complete; cleanup or a single retry is visible.
- Determinism path: repeated runs with the same map/read plan and simulator profile produce equivalent hashes, counts, and statuses even if agent prose differs.

**Verification:** The gate passes only when a human can inspect the report and distinguish live proof, static proof, blocked prerequisites, and unresolved holds. No generated fixture or transcript is committed.

## System-Wide Impact

The campaign adds a native external-system boundary to a public read-only skills repository. It must keep agent and reviewer evidence in the same run directory, preserve the existing artifact contracts, and avoid copying simulator exports or private paths into tracked files. It also creates a release distinction between deterministic exporter tests and native Node-RED acceptance.

## Risks & Dependencies

- **Node-RED availability:** The current Mac environment does not have `node-red` on `PATH`. Treat this as an explicit blocked/not-run prerequisite until a pinned runtime is available.
- **Port ambiguity:** Simulator deployments may expose different host/container ports. Use the reported ready/startup port, never a hard-coded default, and record it in the receipt.
- **Session contamination:** A persisted simulator state or active scenario can make a 10- or 50-unit result look valid. Configure an isolated session, clear scenarios, and record pre/post fleet counts before traffic.
- **Node-RED output seam:** Successful derived messages are not automatically a capture file. The test-only sink/debug export is a required acceptance seam; without it, native live proof is incomplete.
- **Tick-time drift:** Compare stable snapshots for correctness and record same-window timestamps. Treat transient mismatches as inconclusive with one retry.
- **Flow cardinality:** A compiled block may represent one unit or a bounded slice. Derive expected request/unit counts from the read plan and fail on missing or duplicate identities.
- **Recovery cleanup:** Stop scenarios, drain/close clients, and retain partial evidence on success, timeout, import failure, or interruption.

## Verification Contract

- Existing static exporter tests continue to prove deterministic disabled/manual/read-only flow shape.
- New contract tests prove campaign schema, report sanitization, profile/budget bounds, hash and endpoint refusal, and terminal-state rules without requiring Node-RED.
- Native acceptance runs the two Codex journeys against a real local simulator and pinned Node-RED/contrib-node versions. It compares live raw/derived values with the simulator oracle, runs capture analysis, exercises one state change, and exercises one recovery path.
- A native prerequisite failure is recorded as `blocked` or `not-run` with the unmet version/runtime detail; it is not reported as a pass.
- The campaign must leave no tracked source exports, credentials, absolute local paths, or raw transcripts.
- The focused Python test files and `scripts/verify_repo.py` pass before handoff; native evidence is reported separately from that deterministic repository gate.

## Definition of Done

- The runbook, agent task, fixture, runner, and contract tests exist under the planned paths.
- The 10-unit and 50-unit journeys have explicit finite budgets, checkpoints, oracle comparisons, and terminal statuses.
- Read-only, loopback, hash, identity, timeout, state-change, and cleanup invariants are enforced before and during live traffic.
- Native and static evidence are clearly separated, and unavailable native tooling is reported honestly.
- The focused test set passes, and abandoned experimental files or generated captures are removed from the tracked diff.
