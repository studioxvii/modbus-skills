# Format Support

## CSV

Support comma, semicolon, and tab delimiters. Preserve quoted delimiters, escaped quotes, and multiline fields. Report rows with invalid or ambiguous addresses.

A plain `Offset` header may mean an engineering bias or a register location.
Preserve it as `source_offset` with a warning; do not make it a protocol address.
Explicit `Protocol Offset`, `Zero-based Offset`, and `Engineering Offset` retain
their documented roles.

## JSON

Accept a root array or an object containing `registers` or `data`. Preserve known canonical fields and raw unknown source fields.

## XML

Accept repeated `register` or `row` elements. Reject external entities and document type declarations.

## XLSX

Inspect visible worksheets. Prefer a sheet with clear address and name columns. Report the selected sheet and header row. Do not execute formulas or macros.

## Structured text

Treat text rows as candidates. Require review when columns cannot be identified deterministically.
