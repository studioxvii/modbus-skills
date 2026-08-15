# Modbus user paths

Choose the path from the user's outcome and current artifact. Prefer one outcome skill;
use a specialist only when the user explicitly requests that stage.

## Broad setup or an OEM source

For an OEM PDF, spreadsheet, JSON, XML, XLSX, or text file, or for broad setup help
when the user does not name a specialist stage, recommend `compile-user-map`:

```text
OEM PDF, CSV, JSON, XML, XLSX, or text + measurement intent + optional targets
  -> compile-user-map
```

This is the primary route. It completes safe offline work in one run, resumes one
case only when a material exception blocks progress, and leaves live reads separate.
Do not replace it with a parse-review-normalize-plan-builder invocation chain.
Node-RED is one optional target, not the default finish.

## I have a register map

When the requested outcome is source-map review rather than organized user outputs:

```text
PDF or structured source -> review-map
normalized map -> check-map
blocking exception groups -> review-evidence -> apply-review
```

Use `extract-pdf-map` only when extraction evidence itself is the requested outcome.
Use `parse-map` only when candidate rows themselves are the requested outcome.

## I need a polling-tool output

```text
validated map -> plan-reads
one named target -> build-node-red | build-modpoll | build-modscan
several or undecided targets -> build-tool-pack
```

Use `probe` mode while required byte order is unresolved. Use `final` mode only with a
plan bound to the exact validated map.

The Node-RED target uses one start button, runs the plan one request at a time, and
writes `capture.json` directly. Send that file to `analyze-capture`.

## I do not know the byte order

```text
no raw words -> capture-sample
raw words or capture/v1 -> check-byte-order
human layout decision -> apply-review -> plan-reads -> selected builder
```

One physical read supplies every candidate. Evidence never chooses the layout.
Coil and packed-bit numbering is not a byte-order question; keep it as a map hold.

## The values or communications look wrong

```text
bounded JSON or CSV samples -> analyze-capture
raw-word ambiguity -> check-byte-order
suspected map or firmware change -> compare-maps
```

Analysis produces evidence. It does not issue device actions.

## The device or firmware map changed

```text
raw old or new sources -> review-map for each
two validated maps -> compare-maps
validated new map -> plan-reads -> selected builder
```

Map order changes are noise. Route, unit, area, and offset changes are moves.

## I need a different file format

```text
simple documented text or CSV example -> build-custom-export
Node-RED, Modpoll (BETA), or ModScan (BETA) -> use the dedicated builder
opaque or undocumented native format -> stop at a documented interchange plan
```

## Specialist stages

These stages exist for explicit requests or for work that starts from an already
normalized map or capture. They are not the default path from an OEM source:

```text
parse-map | extract-pdf-map | normalize-map | check-map | plan-reads |
build-node-red | build-modpoll | build-modscan | capture-sample |
analyze-capture | check-byte-order
```

Route an explicitly requested stage directly to that skill.

## Routing rule

Recommend the skill that completes the user's outcome with its available inputs.
Choose `compile-user-map` for OEM-source-to-user-output requests and for broad setup
help. Choose `review-map` when source-map review itself is the outcome. Choose a
specialist for explicit extraction, parsing, review, comparison, remapping, capture
analysis, byte-order, planning, or target-only requests. Offer one alternate only
when the current goal is genuinely ambiguous.
