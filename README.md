# Modbus Skills

Read-only Modbus engineering workflows for the OpenAI Codex plugin architecture.

Modbus Skills turns an OEM register map and measurement intent into an organized user
map, JSON, CSV, and optional tool outputs in one resumable run. Focused skills remain
available for extraction, validation, byte order, comparison, and capture analysis.

This repository is private and pre-release. It does not contain private product code, customer data, vendor manuals, or complete vendor register maps.

## Why this exists

Modbus work often fails at the boundaries between a manual, a spreadsheet, an address convention, a polling tool, and the engineer who must approve the result. A small error can shift every register or decode a valid response into the wrong value.

This project makes those boundaries explicit:

- Unknown engineering values become blocking holds.
- One raw read can be evaluated with every supported byte and word layout.
- Clean deterministic work proceeds automatically; a human resolves only genuine
  engineering ambiguity or authorizes the next live device action.
- Read plans use Modbus read function codes 01 through 04 only.
- Generated artifacts are deterministic, checksummed, and traceable to the validated map.

## What you can do

| Task | Result |
| --- | --- |
| Compile an OEM source for a specific use | Organized user map, JSON, CSV, exclusions, and optional target outputs |
| Review CSV, JSON, XML, XLSX, text, or PDF map data | Traceable candidate map, normalized map, lint report, and grouped exception queue |
| Resolve uncertain 32-bit byte order | `ABCD`, `BADC`, `CDAB`, and `DCBA` interpretations from one raw sample |
| Resolve uncertain 64-bit byte order | Every supported 64-bit byte and word layout from one four-word sample |
| Plan device reads | Bounded FC01–FC04 blocks tied to the exact validated map hash |
| Build polling artifacts | Node-RED, Modpoll, Modbus Poll, ModScan, or any selected combination |
| Review firmware changes | Added, removed, moved, and changed points without row-order noise |
| Analyze read data | Communication errors, gaps, duplicates, stale values, flatlines, range issues, rates, and byte-order evidence |

## Quick start

### 1. Install the local marketplace

Clone the repository, then add its marketplace and plugin:

```bash
git clone https://github.com/studioxvii/modbus-skills.git
cd modbus-skills
codex plugin marketplace add "$PWD"
codex plugin add modbus-skills@modbus-skills
```

Private repository access is required during pre-release.

Direct runtime or CLI use requires the project dependencies:

```bash
python3 -m pip install -e .
```

Inside Codex, PDF skills use the bundled workspace Python when available and do not
install dependencies in the task's critical path.

### 2. Choose a skill

Try one of these prompts in Codex:

```text
$compile-user-map Turn this OEM map into an organized user map for temperatures, status, and alarms, with JSON and CSV outputs.
```

```text
$build-tool-pack Build a read-only Node-RED and Modpoll probe for this validated map.
```

```text
$check-byte-order Evaluate every possible byte order for these raw words. Do not choose a winner for me.
```

```text
$compare-maps Compare these firmware maps and show moved, added, removed, and changed points.
```

Use `$modbus-help` when you do not know which skill to use. OEM-source-to-output goals
route to `$compile-user-map`; focused stage requests route to the matching specialist.

All skills require explicit invocation. This keeps unrelated skill instructions out of the agent context.

## Fast OEM map compiler

`$compile-user-map` is the default path from an OEM manual to usable engineering
artifacts. Give it the source and describe the measurements you need; do not manually
work through extraction, normalization, review, selection, and read planning skills.

```text
$compile-user-map Use ./manual.pdf to build a user map for temperatures, operating
status, active alarms, and power. Return the organized map plus JSON and CSV.
```

The skill automatically:

1. preflights the local source and available PDF capability;
2. finds likely register pages, reconciles text coordinates, and automatically falls
   back to drawn-table grid extraction or bounded large-manual discovery;
3. preserves source locations while normalizing the OEM semantics;
4. selects and organizes the requested measurements;
5. validates counts, identities, ranges, datatypes, and artifact hashes; and
6. writes the completed offline bundle before considering optional device targets.

A clean source completes without an approval question. If evidence is genuinely
ambiguous, the result contains one grouped decision packet with affected counts and
source references. Device binding, a physical read, and byte-order confirmation remain
separate gates because they can materially change a requested device-specific output.

Typical output:

```text
case-directory/
├── output/                   # open this folder
│   ├── user-map.md           # compact engineer-readable map
│   ├── user-map.json         # canonical machine-readable output
│   └── user-map.csv          # spreadsheet/import output
├── compile-result.json       # Codex reads this status receipt
├── case.json                 # Codex uses this to resume safely
├── artifacts/                # provenance; normally ignore
└── control/                  # resumable workflow state; normally ignore
```

Excluded or quarantined source items remain in the user map's exception annex rather
than blocking unrelated selected points.

`offline-complete` means bounded source discovery finished without rejected or
quarantined rows and no selected point has a blocking hold. Parser agreement remains
visible evidence, not an extra approval step. Producing valid JSON by itself is not
completion.

For a complete catalog rather than a measurement subset, use selection mode
`all-readable`. It is resolved after extraction, so callers do not need to know OEM
point IDs in advance. Repeated per-point uncertainty is grouped by root cause in the
human bundle while the OEM audit artifact retains point-level evidence.

For deterministic runtime use, create a request described in
[`compile-user-map/references/request.md`](plugins/modbus-skills/skills/compile-user-map/references/request.md)
and run:

```bash
python3 plugins/modbus-skills/skills/compile-user-map/scripts/run.py \
  --request request.json \
  --output ./compile-case
```

Re-running the same request is idempotent. A resume accepts only the typed input named
by `compile-result.json`; stale hashes, broadened decisions, and modified evidence are
rejected without changing the case.

## Choose your path

| Starting point or goal | Start here | Typical next step |
| --- | --- | --- |
| OEM register map or device manual plus desired measurements | `$compile-user-map` | Completed offline bundle or one grouped exception |
| Explicit source-map review only | `$review-map` | Grouped decision packet when needed |
| Polling-tool output | `$plan-reads` | One builder or `$build-tool-pack` |
| Unknown byte order | `$capture-sample` | `$check-byte-order` |
| Bad values or communication | `$analyze-capture` | Evidence review or map comparison |
| Changed device or firmware | `$review-map` for each map | `$compare-maps` |
| Custom text or CSV output | `$build-custom-export` | Human review of the inferred format |

See the [user-path guide](plugins/modbus-skills/references/user-paths.md) for the full high-level map. Each skill gives at most a few relevant handoffs. It does not flood the user with every available skill.

## Core workflows

| Workflow | Purpose | Stops only for |
| --- | --- | --- |
| `compile-user-map` | Compile an OEM source and intent into organized user outputs | One grouped source decision, missing requested target binding, or separate physical evidence |
| `review-register-map` | Parse, normalize, lint, and review in one pass | Grouped blocking exceptions; clean maps continue automatically |
| `determine-byte-order` | Evaluate one immutable raw sample and record a confirmed layout | A human selects one layout with evidence |
| `probe-resolve-finalize-tool-pack` | Build a probe, collect one read, resolve byte order, and rebuild final targets | No final pack before sample and layout review |
| `build-tool-pack` | Generate any combination of Node-RED, Modpoll, and ModScan | The map and exact map-bound plan must pass preflight |
| `analyze-read-data` | Analyze bounded JSON or CSV captures | Missing thresholds or metadata that materially change findings |
| `compare-map-revisions` | Compare validated maps across device or firmware revisions | Ambiguous identity or an explicit acceptance decision |

The workflow definitions are available in [`catalog/workflows.json`](catalog/workflows.json). Artifact contracts are documented in [`docs/contracts/artifacts.md`](docs/contracts/artifacts.md).

## Byte-order workflow

Byte order does not need to be known before the first probe exists.

```mermaid
flowchart LR
    A["Validated map with unresolved byte order"] --> B["Compile bounded read plan"]
    B --> C["Generate a read-only probe"]
    C --> D["Run one physical Modbus read"]
    D --> E["Evaluate every supported layout"]
    E --> F["Human confirms layout with evidence"]
    F --> G["Apply decision and rebuild plan"]
    G --> H["Generate final selected targets"]
```

A Node-RED probe sends one manual read per compiled block. The returned raw words feed all layout calculations. The math does not create more Modbus traffic. The generated flow starts disabled and contains no scheduled, deploy-time, or write nodes.

Modpoll and ModScan probes collect the same raw words. `check-byte-order` then evaluates the saved sample. Evidence never selects a winner. `apply-review` verifies the sample identity and applies only the explicit human decision.

## Generated targets

| Target | Generated output | Important limit |
| --- | --- | --- |
| Node-RED | Disabled importable flow with manual injects, flex getters, response gates, catch paths, and watchdogs | Native import verification is still required |
| `gavinying/modpoll` | Documented `device`, `poll`, and `ref` CSV files | Use the pinned open-source implementation for acceptance testing |
| Witte Modbus Poll | Readable desktop plan, bounded PowerShell automation, or disabled v12 XML | The project does not synthesize opaque `.mbp` or `.mbw` files |
| ModScan | Manual setup, read-plan, point-map, and protocol test-message files | The project does not invent undocumented `.tst` or `.cfg` formats |
| Combined tool pack | Any non-empty target combination with manifests and SHA-256 checksums | All targets use one validated map and one read plan |

## Safety model

The runtime fails closed. It does not generate Modbus writes, broadcast requests, discovery scans, stored credentials, or unbounded polling.

Final output requires these properties to be verified; it does not require a separate
blanket approval when every property is explicit and checks pass:

- A resolved route and unit identifier.
- A confirmed register area and address convention.
- Readable point access.
- A confirmed datatype and word width.
- A confirmed byte order for applicable multi-register values.
- A read plan bound to the SHA-256 hash of the exact validated map.
- A new read plan after any approved map change.

Source `include` and `reviewed` flags remain evidence. They never become repository approval. Write-only and source-excluded points cannot enter an active read plan.

## Skills

| Goal | Skill |
| --- | --- |
| Route an unclear request | `modbus-help` |
| Compile an OEM map into organized user outputs | `compile-user-map` |
| Parse CSV, JSON, XML, XLSX, or delimited text | `parse-map` |
| Extract traceable candidates from a PDF | `extract-pdf-map` |
| Normalize explicit map fields | `normalize-map` |
| Find map errors and blocking holds | `check-map` |
| Run the complete map review chain | `review-map` |
| Review confirmed, inferred, and rejected evidence | `review-evidence` |
| Apply an explicit human review record | `apply-review` |
| Preview address-basis conversions | `remap-addresses` |
| Compare device or firmware maps | `compare-maps` |
| Build a bounded raw-word probe | `capture-sample` |
| Evaluate byte and word layouts from one sample | `check-byte-order` |
| Compile bounded FC01–FC04 read blocks | `plan-reads` |
| Generate a disabled Node-RED read flow | `build-node-red` |
| Generate `gavinying/modpoll` or Witte artifacts | `build-modpoll` |
| Generate an auditable ModScan read plan | `build-modscan` |
| Build any selected target combination | `build-tool-pack` |
| Analyze communication and signal behavior | `analyze-capture` |
| Infer a declarative custom text or CSV format | `build-custom-export` |

## Repository structure

```text
.agents/plugins/marketplace.json       Repository marketplace entry
plugins/modbus-skills/                 Installable OpenAI plugin
plugins/modbus-skills/skills/          One outcome skill and focused specialist skills
plugins/modbus-skills/runtime/         Deterministic Python runtime
catalog/                               Skill, workflow, and activation catalogs
docs/contracts/                        Chaining and artifact contracts
docs/research/                         Researched Modbus problem catalog
research/issues.json                   Problems mapped to sources and skills
tests/                                 Synthetic unit and workflow tests
scripts/                               Build and verification commands
site/                                  Generated agent-search catalog
```

## Agent and search surfaces

The repository provides several search and activation surfaces:

- `catalog/skills.json` contains skill IDs, descriptions, and prompts.
- `catalog/workflows.json` defines skill chains, artifacts, human gates, and stop conditions.
- `catalog/activation-cases.json` contains positive and close-negative activation cases.
- `research/issues.json` maps researched Modbus problems to primary sources and skills.
- `site/llms.txt` and `site/llms-full.txt` provide direct agent-readable indexes.
- The generated site includes canonical links, JSON-LD, Markdown mirrors, JSON mirrors, and a sitemap.

## Verification

Run the complete dependency-free suite:

```bash
python3 scripts/verify_repo.py
```

Current evidence:

- All 20 skills pass the repository validator.
- The plugin passes the official OpenAI plugin validator.
- 308 repository tests pass in the current working tree.
- The outcome-compiler transcript suite passes clean structured intake, automatic PDF
  coordinate fallback, grouped decision/resume, binding preservation, and evidenced
  contiguous-read scenarios.
- The tracked 150-point synthetic benchmark completes the offline bundle in under
  20 ms on the documented macOS arm64 / Python 3.14.6 envelope, against a five-minute
  local threshold. Timing is diagnostic; transcript behavior is the deterministic gate.
- The public synthetic human workflow passes 41 of 41 checks.
- Seven local real-world register maps pass 45 of 45 workflow checks across 31 skill calls.
- Blind novice, commissioning, and reviewer trials pass.
- Independent correctness and security reviews have no open findings.

The real-world maps remain outside this repository because redistribution rights are not confirmed. No workflow test issued live device traffic. Native Node-RED, Modpoll, Witte Modbus Poll, and ModScan acceptance tests remain release gates.

See [`docs/verification-status.md`](docs/verification-status.md) and [`docs/testing.md`](docs/testing.md) for the evidence and test method.

## Development rules

- Keep all generated behavior read-only.
- Preserve source evidence and rejected rows.
- Do not add customer data, vendor manuals, or complete vendor maps.
- Add deterministic tests for each behavior change.
- Run `python3 scripts/verify_repo.py` before each handoff.

## License

Modbus Skills is licensed under the Apache License, Version 2.0. See
[`LICENSE`](LICENSE) for the license terms and [`NOTICE`](NOTICE) for the copyright
notice. Every distributable skill also declares the SPDX identifier `Apache-2.0` in
its `SKILL.md` metadata.

The license applies to the material distributed by this repository. It does not grant
rights to third-party vendor manuals, register maps, customer data, product names, or
tool binaries, none of which are distributed as part of this project.

## Release status

The repository is licensed under Apache-2.0 but remains private and pre-release. Do not
change its visibility or publish a release until every item in
[`docs/publication-checklist.md`](docs/publication-checklist.md) is complete.

The GitHub repository must remain private during pre-release.
