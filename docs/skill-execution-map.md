# Skill execution map

Machine-readable workflow chains live in [`catalog/workflows.json`](../../catalog/workflows.json).
High-level routing lives in [`user-paths.md`](../../plugins/modbus-skills/references/user-paths.md).
This document maps **which skill runs when**, **what it depends on**, and **which script and runtime code execute**.

Legend:

- **Skill** — agent-invoked `SKILL.md` workflow
- **Gate** — human or external step (no `run.py`)
- **Runtime** — deterministic Python in `plugins/modbus-skills/runtime/modbus_skills/`

## Router decision tree

`modbus-help` recommends work only; it runs no script.

```mermaid
flowchart TD
  START["User goal + current artifact"] --> HELP["modbus-help<br/>(reads user-paths.md)"]

  HELP -->|"writes, broadcast, scan, credentials,<br/>unbounded polling"| STOP["Explain read-only boundary and stop"]

  HELP -->|"explicit stage named<br/>(parse, extract, compare, …)"| STAGE["Named specialist skill"]

  HELP -->|"OEM PDF / spreadsheet / JSON / XML /<br/>XLSX / text, or broad setup help"| COMPILE["compile-user-map"]

  HELP -->|"source-map review is the outcome"| REVIEW["review-map"]

  HELP -->|"validated map + named target"| BUILDER["build-node-red | build-modpoll |<br/>build-modscan | build-tool-pack"]

  HELP -->|"capture / sample already in hand"| CAPTURE["analyze-capture | check-byte-order"]

  STAGE --> RUN["Skill runs scripts/run.py"]
  COMPILE --> RUN
  REVIEW --> RUN
  BUILDER --> RUN
  CAPTURE --> RUN
```

**Routing rule:** prefer the skill that completes the stated outcome. Do not replace `compile-user-map` with a parse → normalize → plan → builder chain for OEM-to-output requests.

## Workflow catalog chains

Each workflow in `catalog/workflows.json` is a ordered chain of skills, nested workflows, and gates.

```mermaid
flowchart TB
  subgraph compile["compile-user-map workflow"]
    C1["compile-user-map"]
  end

  subgraph pdf["extract-pdf-register-map"]
    P1["extract-pdf-map"] --> P2["review-evidence"]
    P2 --> P3{{"human-gate:<br/>source exceptions"}}
  end

  subgraph review["review-source-map"]
    R1["review-map"] --> R2{{"human-gate:<br/>map exceptions"}}
    R2 --> R3["apply-review"]
  end

  subgraph byte["confirm-byte-order"]
    B1["check-byte-order"] --> B2{{"human-gate:<br/>layout choice"}}
    B2 --> B3["apply-review"]
  end

  subgraph probe["determine-byte-order"]
    D1["capture-sample"] --> D2{{"external-gate:<br/>one bounded read"}}
    D2 --> D3["confirm-byte-order workflow"]
  end

  subgraph remap["remap-address-notation"]
    M1["remap-addresses"] --> M2["check-map"]
  end

  subgraph custom["build-custom-export workflow"]
    CE1["check-map"] --> CE2["build-custom-export"]
  end

  subgraph pack["build-tool-pack workflow"]
    TP1["check-map"] --> TP2["plan-reads"] --> TP3["build-tool-pack"]
  end

  subgraph finalize["probe-resolve-finalize-tool-pack"]
    F1["plan-reads"] --> F2["build-tool-pack probe"]
    F2 --> F3{{"external-gate:<br/>one bounded read"}}
    F3 --> F4["confirm-byte-order"]
    F4 --> F5["plan-reads"] --> F6["build-tool-pack final"]
  end

  subgraph analyze["analyze-read-data"]
    A1["analyze-capture"] --> A2{{"human-gate:<br/>consequential follow-up"}}
  end

  subgraph diff["compare-map-revisions"]
    CM1["review-source-map ×2"] --> CM2["compare-maps"]
    CM2 --> CM3{{"human-gate:<br/>accept / deploy"}}
  end
```

Gate kinds:

| Kind | Who acts | Typical output |
| --- | --- | --- |
| `human-gate` | Engineer confirms grouped choices | `modbus-review-decisions/v1` or `review-disposition/v1` |
| `external-gate` | Operator runs probe in Node-RED, Modpoll, or ModScan | `capture/v1` (`capture.json`) |

## Artifact dependency graph

Skills consume and produce typed artifacts. Arrows show the usual forward flow; specialist skills can start mid-chain when the artifact already exists.

```mermaid
flowchart LR
  SRC["source-bundle/v1<br/>pdf-source/v1"] --> CAND["candidate-map/v1"]
  CAND --> MAP["modbus-map/v1"]
  MAP --> LINT["modbus-map-lint/v1"]
  MAP --> REV["modbus-map-evidence-review/v1"]
  REV --> DEC["modbus-review-decisions/v1"]
  DEC --> MAP2["reviewed modbus-map/v1"]
  MAP2 --> PLAN["modbus-read-plan/v1"]
  PLAN --> PACK["modbus-tool-pack/v1"]
  PACK --> CAP["capture/v1"]
  CAP --> BO["modbus-byte-order-evidence/v1"]
  BO --> DEC
  CAP --> ANA["modbus-capture-analysis/v1"]
  MAP2 --> DIFF["modbus-map-diff/v1"]
  MAP2 --> EXP["custom-export/v1"]
  MAP --> REMAP["modbus-address-remap/v1"]
  SRC --> OEM["modbus-oem-map/v1"]
  OEM --> UMAP["user-map bundle<br/>md / json / csv"]
  OEM --> COMPILE["modbus-compile-result/v1"]
```

## Script execution reference

Every operational skill (except `modbus-help`) ends in:

```text
python3 plugins/modbus-skills/skills/<skill>/scripts/run.py …
  → modbus_skills.cli.run_cli("<handler>", argv)
  → runtime handler in cli.py
  → domain module(s)
```

| Skill | `run.py` invokes | CLI handler | Primary runtime module(s) |
| --- | --- | --- | --- |
| `compile-user-map` | `compile-user-map` | `_handle_compile` | `compiler.compile_user_map` → `user_map`, `read_plan`, `tool_pack` |
| `parse-map` | `parse-map` | `_handle_parse` | `parsers.parse_source` |
| `extract-pdf-map` | `extract-pdf` | `_handle_pdf` | `pdf_extraction.extract_pdf` |
| `normalize-map` | `normalize-map` | `_handle_normalize` | `map_workflows.normalize_map` |
| `check-map` | `lint-map` | `_handle_lint` | `map_workflows.lint_map` |
| `review-map` | `diagnose-map` | `_handle_diagnose` | `map_workflows.diagnose_map` (see composite below) |
| `review-evidence` | `review-evidence` | `_handle_review` | `map_workflows.review_parse_evidence` (+ optional `lint_map`) |
| `apply-review` | `apply-review-decisions` | `_handle_decisions` | `decisions.apply_review_decisions` |
| `remap-addresses` | `remap-addresses` | `_handle_remap` | `address.resolve_address` (preview / apply in CLI) |
| `compare-maps` | `compare-maps` | `_handle_compare` | `comparison.compare_maps` |
| `capture-sample` | `capture-sample` | `_handle_capture` | `read_plan.compile_read_plan`, `tool_pack.build_tool_pack` (probe) |
| `check-byte-order` | `evaluate-byte-order` | `_handle_byte_order` | `byte_order.evaluate_byte_orders` |
| `plan-reads` | `compile-read-plan` | `_handle_plan` | `read_plan.compile_read_plan` |
| `build-node-red` | `generate-node-red` | `_handle_node_red` | `node_red.export_node_red` |
| `build-modpoll` | `generate-modpoll` | `_handle_modpoll` | `modpoll.export_modpoll` |
| `build-modscan` | `generate-modscan` | `_handle_modscan` | `modscan.export_modscan` |
| `build-tool-pack` | `build-tool-pack` | `_handle_tool_pack` | `tool_pack.build_tool_pack` → target exporters |
| `analyze-capture` | `analyze-capture` | `_handle_analysis` | `analysis.analyze_capture` |
| `build-custom-export` | `infer-custom-format` | `_handle_custom` | `custom_format.validate_custom_format`, `render_custom_format` |
| `modbus-help` | — | — | reads `user-paths.md`; no runtime call |

CLI skill IDs are aliases resolved in `cli.COMMAND_ALIASES` (for example `check-map` → `lint-map`, `review-map` → `diagnose-map`).

### `run.py` command lines (from `SKILL.md`)

| Skill | Typical invocation |
| --- | --- |
| `compile-user-map` | `run.py --request request.json --output <case-dir>` |
| `parse-map` | `run.py --input <path> --output <path>` |
| `extract-pdf-map` | `run.py --input manual.pdf --output <dir> [--pages <range>]` |
| `normalize-map` | `run.py --input candidate.json --output normalized.json` |
| `check-map` | `run.py --input map.json --output validation.json` |
| `review-map` | `run.py --input <path> --output <directory>` |
| `review-evidence` | `run.py --input artifact.json --output report.json` |
| `apply-review` | `run.py --map draft.json --decisions decisions.json [--evidence evidence.json] --output reviewed.json` |
| `remap-addresses` | `run.py --input map.json --from <conv> --to <conv> --output preview.json` |
| `compare-maps` | `run.py --before old.json --after new.json --output diff.json` |
| `capture-sample` | `run.py --request probe.json --output <directory>` |
| `check-byte-order` | `run.py --input capture.json --types uint32,int32,float32 --output evidence.json` |
| `plan-reads` | `run.py --input map.json --output read-plan.json --max-gap <n>` |
| `build-node-red` | `run.py --map map.json --plan read-plan.json --mode <probe\|final> --output <dir>` |
| `build-modpoll` | `run.py --map map.json --plan read-plan.json --profile <profile> --mode <mode> --output <dir>` |
| `build-modscan` | `run.py --map map.json --plan read-plan.json --mode <mode> --output <dir>` |
| `build-tool-pack` | `run.py --request tool-pack-request.json --output <directory>` |
| `analyze-capture` | `run.py --input <capture> --options options.json --output analysis.json` |
| `build-custom-export` | `run.py --example <path> --map map.json --output <directory>` |

## Composite skill internals

Some skills chain multiple runtime steps inside **one** `run.py` invocation.

### `review-map` → `diagnose-map`

```mermaid
flowchart LR
  IN["source file<br/>PDF or structured"] --> DIAG["diagnose_map()"]
  DIAG --> PDF{"PDF?"}
  PDF -->|yes| EX["extract_pdf"]
  PDF -->|no| PAR["parse_source"]
  EX --> NORM["normalize_map"]
  PAR --> NORM
  NORM --> LINT["lint_map"]
  LINT --> REV["review_parse_evidence"]
  REV --> OUT["parsed.json<br/>map-draft.json<br/>lint.json<br/>review.json"]
```

Equivalent specialist chain (only when each stage is the explicit outcome): `extract-pdf-map` or `parse-map` → `normalize-map` → `check-map` → `review-evidence`.

### `compile-user-map`

One case directory; the compiler may pause for human decisions and resume with `--case` + `--resume`.

```mermaid
flowchart TD
  REQ["request.json"] --> VAL["validate request + OEM map"]
  VAL --> SRC{"source exceptions?"}
  SRC -->|yes| SD["awaiting-source-decision"]
  SRC -->|no| BUNDLE["compile_user_map_bundle"]
  BUNDLE --> SEL{"selection packet?"}
  SEL -->|yes| ASD["awaiting-selection-decision"]
  SEL -->|no| UMAP["user-map md/json/csv"]
  UMAP --> HOLD{"blocking holds or<br/>incomplete PDF evidence?"}
  HOLD -->|yes| PART["partial + corrected-source path"]
  HOLD -->|no| TGT{"targets requested?"}
  TGT -->|no| OFF["offline-complete"]
  TGT -->|yes| BIND{"device binding?"}
  BIND -->|no| AB["awaiting-binding"]
  BIND -->|yes| LINK["link_selected_map"]
  LINK --> PLAN["compile_read_plan"]
  PLAN --> PACK["build_tool_pack final"]
  PACK --> DONE["complete | partial |<br/>awaiting-physical-read"]
```

Internal stages are **not** exposed as separate skill invocations during a normal compile.

### `capture-sample` and `build-tool-pack`

Both call `build_tool_pack`, which fans out to target exporters:

```mermaid
flowchart LR
  MAP["modbus-map/v1"] --> TP["build_tool_pack"]
  PLAN["modbus-read-plan/v1"] --> TP
  TP --> NR["export_node_red"]
  TP --> MP["export_modpoll"]
  TP --> MS["export_modscan"]
  NR --> OUT["tool-pack artifacts + zip"]
  MP --> OUT
  MS --> OUT
```

`capture-sample` builds **probe** mode only and stops before the live read. `build-tool-pack` uses the requested mode (`probe` or `final`).

### `check-byte-order` → `apply-review` (byte-order path)

Evidence does not pick a layout. A human records one layout in `modbus-review-decisions/v1`; `apply-review` writes the confirmed map. Downstream: `plan-reads` → builder or `build-tool-pack`.

## User-path quick reference

Condensed from `user-paths.md`:

| Situation | Path |
| --- | --- |
| OEM source → organized outputs | `compile-user-map` |
| Source-map review outcome | `review-map` → (`apply-review` after human gate) |
| Validated map → tool files | `plan-reads` → one builder or `build-tool-pack` |
| Unknown byte order, no sample | `capture-sample` → external read → `check-byte-order` → `apply-review` → `plan-reads` → builder |
| Bad values / comms | `analyze-capture` |
| Firmware / map revision | `review-map` (each side) → `compare-maps` |
| Custom text/CSV format | `check-map` → `build-custom-export` |
| Address notation change | `remap-addresses` → `check-map` |

## Excalidraw note

This file uses Mermaid for version-controlled, diff-friendly diagrams. For whiteboard-style layout in Excalidraw:

1. Import the workflow subgraphs above as separate frames (router, catalog, artifacts, composites).
2. Use the script table for node labels on each skill box (`run.py` → handler → module).
3. Gate nodes should be dashed shapes distinct from skill rectangles.
