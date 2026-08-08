# Format Support

## CSV

Support comma, semicolon, and tab delimiters. Preserve quoted delimiters, escaped quotes, and multiline fields. Report rows with invalid or ambiguous addresses.

## JSON

Accept a root array or an object containing `registers` or `data`. Preserve known canonical fields and raw unknown source fields.

## XML

Accept repeated `register` or `row` elements. Reject external entities and document type declarations.

## XLSX

Inspect visible worksheets. Prefer a sheet with clear address and name columns. Report the selected sheet and header row. Do not execute formulas or macros.

## Structured text

Treat text rows as candidates. Require review when columns cannot be identified deterministically.
