# Modbus user paths

Choose the path from the user's goal and current artifact. Recommend the first missing step, not the full catalog.

## I have a register map

```text
PDF manual -> $extract-pdf-map
CSV, JSON, XML, XLSX, or text -> $parse-map
candidate map -> $normalize-map
normalized map -> $check-map -> $review-evidence -> $apply-review
reviewed map -> $plan-reads
```

Use `$review-map` when the user wants the complete source-to-decision workflow instead of selecting each stage.

## I need a polling-tool output

```text
reviewed map -> $plan-reads
one named target -> $build-node-red | $build-modpoll | $build-modscan
several or undecided targets -> $build-tool-pack
```

Use `probe` mode while required byte order is unresolved. Use `final` mode only with a plan bound to the exact reviewed map.

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
two reviewed maps -> $compare-maps
approved new map -> $plan-reads -> selected builder
```

Map order changes are noise. Route, unit, area, and offset changes are moves.

## I need a different file format

```text
simple documented text or CSV example -> $build-custom-export
Node-RED, Modpoll, or ModScan -> use the dedicated builder
opaque or undocumented native format -> stop at a documented interchange plan
```

## Routing rule

Recommend one next skill with its required input and output artifact. Show the full path only when the user asks for it. Offer one alternate only when the current goal is genuinely ambiguous.
