# Research Method

## Problem selection

Add a problem only when engineers can state it as an observable Modbus task or failure. Prefer repeated issues that a focused skill can solve with a deterministic artifact.

## Source order

Use this order:

1. The Modbus application protocol specification.
2. Official target-tool documentation and published examples.
3. Maintainer issue trackers and discussions with reproducible behavior.

Do not use search-result text as evidence. Link to the source record.

## Public boundary

- Store links and short original summaries.
- Do not store vendor manuals or complete vendor maps.
- Use synthetic fixtures.
- Review private tools only for general failure classes and test ideas.
- Do not copy private source, fixtures, identifiers, prompts, paths, or product behavior.

## Traceability

Each record in `research/issues.json` must identify related skill IDs. Each deterministic skill must have a synthetic test for its main failure class. A target adapter also needs a native application test before publication claims that the target verified the output.
