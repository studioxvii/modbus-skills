# Modbus Skills

Modbus Skills is a public-ready collection of read-only engineering workflows for ChatGPT and Codex.

The repository contains focused skills, a deterministic local runtime, synthetic test fixtures, and a repo-local OpenAI plugin marketplace. Every tracked file is intended to be safe for future public review.

It solves common Modbus register-map, address, byte-order, polling, configuration, and capture-analysis problems. It does not contain product code or private customer data.

## Main workflows

- Parse and review Modbus register maps.
- Normalize explicit address conventions without guessing.
- Find duplicates, overlaps, unsafe widths, and unresolved fields.
- Evaluate common byte and word orders from one raw sample.
- Compile bounded read plans for function codes 01 through 04.
- Generate Node-RED, Modpoll, and ModScan read artifacts.
- Build any selected combination as one checksummed tool pack.
- Analyze bounded live or recorded captures.

## Skills

| Goal | Skill |
|---|---|
| Route an unclear Modbus request | `ask-modbus` |
| Parse CSV, JSON, XML, XLSX, or delimited text | `parse-modbus-map` |
| Extract traceable candidates from a PDF | `extract-modbus-map-from-pdf` |
| Normalize explicit map fields | `normalize-modbus-map` |
| Find map errors and blocking holds | `lint-modbus-map` |
| Run the complete map review chain | `diagnose-modbus-map` |
| Review confirmed, inferred, and rejected evidence | `review-modbus-evidence` |
| Preview address-basis conversions | `remap-modbus-addresses` |
| Compare device or firmware maps | `compare-modbus-maps` |
| Build a bounded raw-word probe | `capture-modbus-sample` |
| Evaluate byte and word layouts from one sample | `evaluate-modbus-byte-order` |
| Compile bounded FC01–FC04 read blocks | `compile-modbus-read-plan` |
| Generate a disabled Node-RED read flow | `generate-node-red-flow` |
| Generate gavinying/modpoll or Witte artifacts | `generate-modpoll-config` |
| Generate an auditable ModScan read plan | `generate-modscan-config` |
| Build any selected target combination | `build-modbus-tool-pack` |
| Analyze communication and signal behavior | `analyze-modbus-capture` |
| Infer a declarative custom text or CSV format | `infer-custom-modbus-export-format` |

## Byte-order workflow

Byte order does not need to be known before the first tool artifact exists.

1. Compile a bounded read plan.
2. Generate a `probe` artifact for Node-RED, Modpoll, ModScan, or a selected combination.
3. Run one physical read and save the raw 16-bit words.
4. Evaluate all supported byte and word layouts from the same immutable sample.
5. Confirm the layout with engineering evidence.
6. Regenerate the selected targets in `final` mode.

In a Node-RED probe, one physical read node feeds all four 32-bit layouts in real time: `ABCD`, `BADC`, `CDAB`, and `DCBA`. Each layout includes `uint32`, `int32`, and `float32` values from the same immutable raw words. This math does not create more Modbus traffic.

Modpoll and ModScan probe artifacts collect the same raw words. The byte-order skill evaluates them after the one-read external gate. The evaluator reports candidates. It does not select a winner.

## Install in Codex

For local development:

```bash
codex plugin marketplace add /absolute/path/to/modbus-skills
codex plugin add modbus-skills@modbus-skills
```

After this repository becomes public, the marketplace can also be added from `studioxvii/modbus-skills`.

## Safety boundary

The project generates read-only artifacts. It does not generate Modbus writes, broadcast requests, network discovery scans, or unbounded polling.

Unresolved engineering choices become holds. A final artifact requires confirmed address basis, register area, unit identifier, datatype, and applicable byte order.

## Repository layout

```text
.agents/plugins/marketplace.json       Repo-local plugin catalog
plugins/modbus-skills/                 Installable OpenAI plugin
plugins/modbus-skills/skills/          Focused skills
plugins/modbus-skills/runtime/         Deterministic Python runtime
catalog/                               Machine-readable skill catalog
docs/contracts/                        Chaining and artifact contracts
tests/                                 Synthetic unit and workflow tests
scripts/                               Repository validation commands
site/                                  Generated public catalog source
```

## Agent and search surfaces

- `.agents/plugins/marketplace.json` is the installable marketplace entry.
- `catalog/skills.json` lists stable skill IDs, descriptions, and prompts.
- `catalog/workflows.json` defines artifact-based skill chains and stop conditions.
- `catalog/activation-cases.json` contains positive and close-negative activation cases.
- `research/issues.json` maps researched Modbus problems to primary sources and skills.
- `site/llms.txt` and `site/llms-full.txt` provide direct agent-readable indexes.
- `site/sitemap.xml`, canonical links, JSON-LD, Markdown mirrors, and JSON mirrors support search indexing.

## Verify locally

```bash
python3 scripts/verify_repo.py
```

The GitHub repository must remain private until the publication checklist is complete. A final open-source license is also required before public release.
