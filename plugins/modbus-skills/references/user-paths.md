# Modbus user paths

Choose the path from the user's outcome and current artifact. Prefer one outcome skill;
use a specialist only when the user explicitly requests that stage.

## I need an organized user map or outputs from an OEM source

```text
OEM PDF, CSV, JSON, XML, XLSX, or text + measurement intent + optional targets
  -> $compile-user-map
```

This is the primary route. It completes safe offline work in one run, resumes one
case only when a material exception blocks progress, and leaves live reads separate.
Do not replace it with a parse-review-normalize-plan-builder invocation chain.

## I have a register map

```text
PDF manual -> $extract-pdf-map
CSV, JSON, XML, XLSX, or text -> $parse-map
candidate map -> $normalize-map
normalized map -> $check-map
clean validated map -> $plan-reads
blocking exception groups -> $review-evidence -> $apply-review -> $plan-reads
```

Use `$review-map` only when the user explicitly wants source-map review rather than an
organized user map or output bundle.

## I need a polling-tool output

```text
validated map -> $plan-reads
one named target -> $build-node-red | $build-modpoll | $build-modscan
several or undecided targets -> $build-tool-pack
```

Use `probe` mode while required byte order is unresolved. Use `final` mode only with a
plan bound to the exact validated map.

## I do not know the byte order

```text
no raw words -> $capture-sample
raw words or capture/v1 -> $check-byte-order
human layout decision -> $apply-review -> $plan-reads -> selected builder
```

One physical read supplies every candidate. Evidence never chooses the layout.

## The values or communications look wrong

```text
bounded JSON or CSV samples -> $analyze-capture
raw-word ambiguity -> $check-byte-order
suspected map or firmware change -> $compare-maps
```

Analysis produces evidence. It does not issue device actions.

## The device or firmware map changed

```text
raw old or new sources -> $review-map for each
two validated maps -> $compare-maps
validated new map -> $plan-reads -> selected builder
```

Map order changes are noise. Route, unit, area, and offset changes are moves.

## I need a different file format

```text
simple documented text or CSV example -> $build-custom-export
Node-RED, Modpoll, or ModScan -> use the dedicated builder
opaque or undocumented native format -> stop at a documented interchange plan
```

## Routing rule

Recommend the skill that completes the user's outcome with its available inputs.
Choose `$compile-user-map` for OEM-source-to-user-output requests. Choose a specialist
for explicit extraction, parsing, review, comparison, remapping, capture analysis,
byte-order, planning, or target-only requests. Show a sequence only when asked. Offer
one alternate only when the current goal is genuinely ambiguous.
