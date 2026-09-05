# Format Support

## CSV

Support comma, semicolon, and tab delimiters. Preserve quoted delimiters, escaped quotes, and multiline fields. Report rows with invalid or ambiguous addresses.

A plain `Offset` header may mean an engineering bias or a register location.
Preserve it as `source_offset` with a warning; do not make it a protocol address.
Explicit `Protocol Offset`, `Zero-based Offset`, and `Engineering Offset` retain
their documented roles.

## JSON

Accept a root array or an object containing `registers`, `data`, `records`, or
`points`. Candidate and canonical wrappers do not require a manual copy/rewrite
before source inspection. Preserve known canonical fields and raw unknown source fields.

For `compile-user-map` source paths, raw arrays and untyped collection objects use
the same header-alias parsing as `parse-map`, including explicit access and function
codes. Typed `candidate-map/v1` (`records`) and `modbus-map/v1` (`points`) inputs keep
their metadata and original row provenance; typed engineering offsets are not raw
address headers. Other explicit artifact schemas and malformed typed collections
are held as unsupported input, not silently treated as canonical records.

## XML

Accept repeated `register` or `row` elements. Reject external entities and document type declarations.

## XLSX

Inspect visible worksheets. Prefer a sheet with clear address and name columns. Report the selected sheet and header row. Do not execute formulas or macros.

## Structured text

Treat text rows as candidates. Require review when columns cannot be identified deterministically.
