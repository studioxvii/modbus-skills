# Modbus Skills

Turn vendor Modbus documentation into clear, usable engineering files.

Modbus Skills helps controls, commissioning, and integration engineers turn a
vendor manual, spreadsheet, PDF, or existing register map into:

- a human-readable user map
- JSON and CSV exports
- bounded read plans
- optional Node-RED, Modpoll, Modbus Poll, or ModScan files

It can also compare firmware revisions, investigate byte order, and analyze
captured Modbus data.

The tools are read-only by design. They never generate write commands,
discovery scans, or unlimited polling. When the documentation is unclear, they
show you what needs a decision instead of guessing.

## Who this is for

Use Modbus Skills when you need to turn vendor documentation into something an
engineer or polling tool can use. It is useful for controls engineers,
commissioning teams, system integrators, and anyone reviewing a device map or
captured Modbus data.

## What you can do

| If you have... | You can... |
| --- | --- |
| A vendor manual or register map | Build a user map for the measurements you need |
| A PDF, spreadsheet, CSV, JSON, XML, or text file | Extract and check register information |
| Confusing 32-bit or 64-bit values | Compare possible byte and word orders |
| Polling-tool output | Build a bounded read plan |
| A new device or firmware revision | Compare added, removed, moved, and changed points |
| Captured read data | Find communication errors, stale values, gaps, bad scaling, and flatlines |

## Quick start

### Install in Codex

Clone the repository and add it as a local Codex plugin:

```bash
git clone https://github.com/studioxvii/modbus-skills.git
cd modbus-skills
codex plugin marketplace add "$PWD"
codex plugin add modbus-skills@modbus-skills
```

Private repository access is required while the project is pre-release.

### Try a workflow

If you have a vendor manual, start here:

```text
$compile-user-map Use ./manual.pdf to create a user map for temperatures,
operating status, alarms, and power. Return Markdown, JSON, and CSV files.
```

Other common workflows:

```text
$check-byte-order Evaluate the possible byte orders for these raw words.
```

```text
$compare-maps Compare these firmware maps and show moved, added, removed, and changed points.
```

```text
$build-tool-pack Build a read-only Node-RED and Modpoll tool pack for this validated map.
```

Each workflow is available separately, so you can choose exactly what you want
to run. Use `$modbus-help` if you are not sure where to start.

### Run the local tools directly

Direct runtime or CLI use requires the project dependencies:

```bash
python3 -m pip install -e .
```

PDF workflows use the bundled workspace Python when it is available.

## From a vendor manual to a usable map

`$compile-user-map` is the main path from a vendor manual to engineering files.
Give it a source file and describe the measurements you need. It will:

1. check that the source can be read;
2. find the relevant register information, including information in PDF tables;
3. preserve links back to the source;
4. standardize names, addresses, units, and data types;
5. check the selected points for missing or conflicting information; and
6. create the requested Markdown, JSON, and CSV files.

If something is genuinely unclear, the workflow pauses with a short list of
specific questions. It does not guess. Unclear points are kept visible so they
can be reviewed later without blocking unrelated points.

Typical user-facing output:

```text
compile-case/
└── output/
    ├── user-map.md
    ├── user-map.json
    └── user-map.csv
```

The case also keeps source references and resume information so the work can be
checked or continued later.

For a complete catalog instead of a measurement subset, use selection mode
`all-readable`. For repeatable command-line runs, see the request format in
[`compile-user-map/references/request.md`](plugins/modbus-skills/skills/compile-user-map/references/request.md):

```bash
python3 plugins/modbus-skills/skills/compile-user-map/scripts/run.py \
  --request request.json \
  --output ./compile-case
```

Running the same request again produces the same result. A resume only accepts
the exact input and evidence recorded for that case; changed evidence or a
broadened decision is rejected without changing the existing case.

## What are you trying to do?

| You have... | Use... | You will get... |
| --- | --- | --- |
| A vendor manual and the measurements you need | `$compile-user-map` | A completed user map or a short list of questions |
| A source map that needs checking | `$review-map` | A reviewed map with any unclear points called out |
| Polling-tool output | `$plan-reads` | A bounded read plan |
| An unknown byte order | `$capture-sample`, then `$check-byte-order` | Possible interpretations of one raw sample |
| Bad values or communication problems | `$analyze-capture` | Evidence about errors, gaps, stale values, and signal behavior |
| A changed device or firmware revision | `$review-map`, then `$compare-maps` | A clear revision comparison |
| A custom text or CSV format | `$build-custom-export` | An output in the requested format |

See the [user-path guide](plugins/modbus-skills/references/user-paths.md) for
more detail.

## When byte order is unclear

A first read does not need to use the final byte order. The tools can collect
one read-only sample, show every supported interpretation, and let you choose
the interpretation supported by the manual or a known device value.

```mermaid
flowchart LR
    A["Validated map with unknown byte order"] --> B["Create a bounded read plan"]
    B --> C["Generate a read-only probe"]
    C --> D["Run one physical Modbus read"]
    D --> E["Show every supported interpretation"]
    E --> F["Engineer confirms the correct layout"]
    F --> G["Apply the decision and rebuild the plan"]
    G --> H["Generate the final tool files"]
```

A Node-RED probe sends one manual read per compiled block. The returned raw
words are reused for all layout calculations; the calculations do not create
additional Modbus traffic. Generated flows start disabled and contain no
scheduled, deploy-time, or write nodes.

Modpoll and ModScan probes collect the same raw words. The saved sample can then
be checked separately before the selected layout is applied.

## Files you can create

| Tool or format | What you get | Note |
| --- | --- | --- |
| Node-RED | A disabled, importable read-only flow with manual injects, response checks, error paths, and watchdogs | Native import verification is still required |
| `gavinying/modpoll` | Documented `device`, `poll`, and `ref` CSV files | Use the pinned open-source implementation for acceptance testing |
| Witte Modbus Poll | A readable desktop plan, bounded PowerShell automation, or disabled v12 XML | The project does not synthesize undocumented binary formats |
| ModScan | Manual setup, read-plan, point-map, and protocol test-message files | The project does not invent undocumented configuration formats |
| Combined tool pack | Any selected combination with manifests and SHA-256 checksums | All files use one validated map and one read plan |

## Safety

The tools are read-only by design. They do not generate:

- Modbus write commands
- broadcast requests
- discovery scans
- stored credentials
- unlimited or scheduled polling

Before creating a final read plan, the workflow checks:

- the device connection and unit ID;
- the register type and address numbering;
- readable point access;
- the data type and word size; and
- the byte order for values that use multiple registers.

Read plans are tied to the SHA-256 hash of the exact validated map. If an
approved map changes, a new read plan is required. Write-only and source-
excluded points cannot enter a final read plan.

## Complete workflow list

| Goal | Workflow |
| --- | --- |
| Route an unclear request | `modbus-help` |
| Compile an OEM map into organized user outputs | `compile-user-map` |
| Parse CSV, JSON, XML, XLSX, or delimited text | `parse-map` |
| Extract traceable candidates from a PDF | `extract-pdf-map` |
| Normalize explicit map fields | `normalize-map` |
| Find map errors and unclear points | `check-map` |
| Run the complete map review chain | `review-map` |
| Review confirmed, inferred, and rejected evidence | `review-evidence` |
| Apply an explicit engineering review | `apply-review` |
| Preview address-basis conversions | `remap-addresses` |
| Compare device or firmware maps | `compare-maps` |
| Build a bounded raw-word probe | `capture-sample` |
| Evaluate byte and word layouts from one sample | `check-byte-order` |
| Compile bounded FC01–FC04 read blocks | `plan-reads` |
| Generate a disabled Node-RED read flow | `build-node-red` |
| Generate `gavinying/modpoll` or Witte files | `build-modpoll` |
| Generate an auditable ModScan read plan | `build-modscan` |
| Build any selected tool combination | `build-tool-pack` |
| Analyze communication and signal behavior | `analyze-capture` |
| Build a custom text or CSV export | `build-custom-export` |

## Repository layout

```text
.agents/plugins/marketplace.json       Local marketplace entry
plugins/modbus-skills/                 Installable Codex plugin
plugins/modbus-skills/skills/          Workflow definitions and instructions
plugins/modbus-skills/runtime/         Deterministic Python runtime
catalog/                               Skill and workflow catalogs
docs/contracts/                        Output and workflow contracts
docs/research/                         Research notes and problem catalog
research/issues.json                   Modbus problems mapped to workflows
tests/                                 Synthetic unit and workflow tests
scripts/                               Build and verification commands
site/                                  Generated documentation indexes
```

The full workflow definitions are in
[`catalog/workflows.json`](catalog/workflows.json). Output contracts are
documented in [`docs/contracts/artifacts.md`](docs/contracts/artifacts.md).

## Verification

Run the repository checks with:

```bash
python3 scripts/verify_repo.py
```

The checks cover workflow structure, generated outputs, read-only behavior,
workflow tests, and synthetic map fixtures.

See [`docs/verification-status.md`](docs/verification-status.md) and
[`docs/testing.md`](docs/testing.md) for current results and the test method.

No workflow test issues live device traffic. Native Node-RED, Modpoll, Witte
Modbus Poll, and ModScan acceptance tests remain release gates.

## For contributors

- Keep all generated behavior read-only.
- Preserve source references and rejected rows.
- Use synthetic fixtures instead of customer data, vendor manuals, or complete vendor maps.
- Add tests for behavior changes.
- Run `python3 scripts/verify_repo.py` before opening a pull request.

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
