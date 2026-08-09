# Compile request

Use one JSON request for a new run or resume. Keep paths local and relative to the
request when practical.

## New run

Include:

- one OEM source path;
- the measurement intent in the user's words or a typed selection;
- optional target IDs and explicit target options;
- optional device binding only when known.

For a complete readable register catalog, encode the user's intent without knowing
point IDs in advance:

```json
{
  "selection_template": {
    "schema_version": "modbus-user-selection-template/v1",
    "requested_measurements": ["all documented Modbus read points"],
    "mode": "all-readable"
  }
}
```

`all-readable` includes every point not explicitly marked write-only after extraction.
Do not send empty `included`, `suggested`, and `excluded` arrays for a complete-map
request; that means no selection.

Do not request route, unit, byte order, target, or page selection before the runtime
reports that the missing value blocks a requested output. An offline user map does not
require device binding.

## Resume

Include the existing case reference and exactly one typed input requested by its
`next_action`: a source-decision candidate, binding, immutable capture, or byte-order
decision. Copy the expected case, phase, input, and packet hashes exactly. A plain-
language reply may be translated into the offered candidate shape, but deterministic
runtime validation remains authoritative.

## Result

Treat `compile-result.json` as the status interface. Report completed artifacts first,
then grouped exclusions or holds, then the one permitted next action. Never replay
completed stages manually or turn one decision packet into page, row, or point prompts.
