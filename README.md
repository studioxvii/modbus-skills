# Modbus Skills

Read-only Modbus engineering workflows for the OpenAI Codex plugin architecture.

Modbus Skills turns register maps, raw register samples, and bounded captures into reviewed engineering artifacts. It can parse and validate maps, evaluate byte order from one read, compile safe read plans, generate tool files, compare revisions, and analyze captured data.

This repository is private and pre-release. It does not contain private product code, customer data, vendor manuals, or complete vendor register maps.

## Why this exists

Modbus work often fails at the boundaries between a manual, a spreadsheet, an address convention, a polling tool, and the engineer who must approve the result. A small error can shift every register or decode a valid response into the wrong value.

This project makes those boundaries explicit:

- Unknown engineering values become blocking holds.
- One raw read can be evaluated with every supported byte and word layout.
- A human must approve engineering decisions before final output.
- Read plans use Modbus read function codes 01 through 04 only.
- Generated artifacts are deterministic, checksummed, and traceable to the reviewed map.

## What you can do

| Task | Result |
| --- | --- |
| Review CSV, JSON, XML, XLSX, text, or PDF map data | Traceable candidate map, normalized map, lint report, and review queue |
| Resolve uncertain 32-bit byte order | `ABCD`, `BADC`, `CDAB`, and `DCBA` interpretations from one raw sample |
| Resolve uncertain 64-bit byte order | Every supported 64-bit byte and word layout from one four-word sample |
| Plan device reads | Bounded FC01–FC04 blocks tied to the exact reviewed map hash |
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

### 2. Start with a plain-language request

Try one of these prompts in Codex:

```text
Review this Modbus register map. Preserve every source row and do not guess missing fields.
```

```text
Build a read-only Node-RED and Modpoll probe for this reviewed map.
```

```text
Evaluate every possible byte order for these raw words. Do not choose a winner for me.
```

```text
Compare these two firmware register maps and show moved, added, removed, and changed points.
```

The `ask-modbus` skill routes an unclear request to the correct focused skill or workflow.

## Core workflows

| Workflow | Purpose | Human stop |
| --- | --- | --- |
| `review-register-map` | Parse, normalize, lint, review, and apply explicit decisions | Every blocking hold must be resolved or excluded |
| `determine-byte-order` | Evaluate one immutable raw sample and record a confirmed layout | A human selects one layout with evidence |
| `probe-resolve-finalize-tool-pack` | Build a probe, collect one read, resolve byte order, and rebuild final targets | No final pack before sample and layout review |
| `build-tool-pack` | Generate any combination of Node-RED, Modpoll, and ModScan | The map and exact map-bound plan must pass preflight |
| `analyze-read-data` | Analyze bounded JSON or CSV captures | A human reviews thresholds and alternative causes |
| `compare-map-revisions` | Compare reviewed maps across device or firmware revisions | A human reviews location and field changes |

The workflow definitions are available in [`catalog/workflows.json`](catalog/workflows.json). Artifact contracts are documented in [`docs/contracts/artifacts.md`](docs/contracts/artifacts.md).

## Byte-order workflow

Byte order does not need to be known before the first probe exists.

```mermaid
flowchart LR
    A["Reviewed map with unresolved byte order"] --> B["Compile bounded read plan"]
    B --> C["Generate a read-only probe"]
    C --> D["Run one physical Modbus read"]
    D --> E["Evaluate every supported layout"]
    E --> F["Human confirms layout with evidence"]
    F --> G["Apply decision and rebuild plan"]
    G --> H["Generate final selected targets"]
```

A Node-RED probe sends one manual read per compiled block. The returned raw words feed all layout calculations. The math does not create more Modbus traffic. The generated flow starts disabled and contains no scheduled, deploy-time, or write nodes.

Modpoll and ModScan probes collect the same raw words. `evaluate-modbus-byte-order` then evaluates the saved sample. Evidence never selects a winner. `apply-modbus-review-decisions` verifies the sample identity and applies only the explicit human decision.

## Generated targets

| Target | Generated output | Important limit |
| --- | --- | --- |
| Node-RED | Disabled importable flow with manual injects, flex getters, response gates, catch paths, and watchdogs | Native import verification is still required |
| `gavinying/modpoll` | Documented `device`, `poll`, and `ref` CSV files | Use the pinned open-source implementation for acceptance testing |
| Witte Modbus Poll | Readable desktop plan, bounded PowerShell automation, or disabled v12 XML | The project does not synthesize opaque `.mbp` or `.mbw` files |
| ModScan | Manual setup, read-plan, point-map, and protocol test-message files | The project does not invent undocumented `.tst` or `.cfg` formats |
| Combined tool pack | Any non-empty target combination with manifests and SHA-256 checksums | All targets use one reviewed map and one read plan |

## Safety model

The runtime fails closed. It does not generate Modbus writes, broadcast requests, discovery scans, stored credentials, or unbounded polling.

Final output requires:

- A resolved route and unit identifier.
- A confirmed register area and address convention.
- Readable point access.
- A confirmed datatype and word width.
- A confirmed byte order for applicable multi-register values.
- A read plan bound to the SHA-256 hash of the exact reviewed map.
- A new read plan after any approved map change.

Source `include` and `reviewed` flags remain evidence. They never become repository approval. Write-only and source-excluded points cannot enter an active read plan.

## Skills

| Goal | Skill |
| --- | --- |
| Route an unclear request | `ask-modbus` |
| Parse CSV, JSON, XML, XLSX, or delimited text | `parse-modbus-map` |
| Extract traceable candidates from a PDF | `extract-modbus-map-from-pdf` |
| Normalize explicit map fields | `normalize-modbus-map` |
| Find map errors and blocking holds | `lint-modbus-map` |
| Run the complete map review chain | `diagnose-modbus-map` |
| Review confirmed, inferred, and rejected evidence | `review-modbus-evidence` |
| Apply an explicit human review record | `apply-modbus-review-decisions` |
| Preview address-basis conversions | `remap-modbus-addresses` |
| Compare device or firmware maps | `compare-modbus-maps` |
| Build a bounded raw-word probe | `capture-modbus-sample` |
| Evaluate byte and word layouts from one sample | `evaluate-modbus-byte-order` |
| Compile bounded FC01–FC04 read blocks | `compile-modbus-read-plan` |
| Generate a disabled Node-RED read flow | `generate-node-red-flow` |
| Generate `gavinying/modpoll` or Witte artifacts | `generate-modpoll-config` |
| Generate an auditable ModScan read plan | `generate-modscan-config` |
| Build any selected target combination | `build-modbus-tool-pack` |
| Analyze communication and signal behavior | `analyze-modbus-capture` |
| Infer a declarative custom text or CSV format | `infer-custom-modbus-export-format` |

## Repository structure

```text
.agents/plugins/marketplace.json       Repository marketplace entry
plugins/modbus-skills/                 Installable OpenAI plugin
plugins/modbus-skills/skills/          Nineteen focused skills
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

- `catalog/skills.json` contains stable skill IDs, descriptions, and prompts.
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

- 19 skills pass the official skill validator.
- The plugin passes the official OpenAI plugin validator.
- 247 repository tests pass from a clean checkout.
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

## Release status

This repository does not yet have an open-source license. Do not publish or redistribute it until the items in [`docs/publication-checklist.md`](docs/publication-checklist.md) are complete.

The GitHub repository must remain private during pre-release.
