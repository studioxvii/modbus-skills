# Compile request

Use one JSON request for a new run or resume. Keep paths local and relative to the
request when practical.

## New run

Include:

- one OEM source path;
- the measurement intent in the user's words plus a typed selection;
- optional target IDs and explicit target options;
- optional device binding only when known.

For a curated measurement set, first inspect the source with the same bounded parser
or PDF extraction workflow used by this plugin. Match the user's words only to unique,
directly evidenced OEM point IDs or exact point names. Put direct matches in
`included`, plausible or ambiguous matches in `suggested`, and known non-matches in
`excluded`. Never treat category words such as "temperatures" or "alarms" as runtime
selectors by themselves.

For a PDF, the bounded inspection command is:

```bash
python3 <plugin-dir>/skills/extract-pdf-map/scripts/run.py \
  --input <manual.pdf> \
  --output <inspection-directory>
```

Use `pdf-extraction.json` from that directory to obtain exact point names, stable IDs,
and evidence references. For structured sources, use the equivalent `parse-map`
entrypoint and its candidate-map output.

This example shows the complete request shape after source inspection. Replace its
names and evidence references with values from the actual source:

```json
{
  "schema_version": "modbus-compile-request/v1",
  "source": {"path": "./manual.pdf", "format": "pdf"},
  "selection_template": {
    "schema_version": "modbus-user-selection-template/v1",
    "requested_measurements": ["temperatures", "active alarms"],
    "included": [
      {
        "exact_name": "Ambient Temperature",
        "matched_intent": "temperatures",
        "match_quality": "exact",
        "reason": "The unique OEM name directly matches the requested measurement.",
        "evidence_refs": ["pdf:page:12:table:1:row:4"]
      }
    ],
    "suggested": [
      {
        "exact_name": "Alarm Status",
        "matched_intent": "active alarms",
        "match_quality": "near",
        "reason": "The OEM name is relevant, but the active-state semantics need confirmation.",
        "evidence_refs": ["pdf:page:13:table:1:row:2"]
      }
    ],
    "excluded": []
  },
  "targets": [],
  "target_options": {}
}
```

The compiler binds each `exact_name` only when it uniquely matches the derived OEM
map. Use `oem_point_id` instead when source inspection already supplied the stable ID.
Suggested entries produce one grouped selection decision; do not silently promote
them to included points.

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
`next_action`: a selection decision, binding, immutable capture, or byte-order
decision. Copy the expected case, phase, input, and packet hashes exactly. A plain-
language reply may be translated into the offered candidate shape, but deterministic
runtime validation remains authoritative.

`provide-corrected-source` is not a resume. Copy the original request, replace the
source path or typed source data, choose a new empty case directory, and start a new
run. Keep the previous partial map files available while the corrected case runs.

## Result

Treat `compile-result.json` as the status interface. Report completed artifacts first,
then grouped exclusions or holds, then the one permitted next action. Never replay
completed stages manually or turn one decision packet into page, row, or point prompts.
The three human deliverables are the only files in `output/`. A result is not complete
when source coverage is unknown, selected PDF fields lack confirmed source evidence, or
any selected point still has a blocking hold. A `partial` result keeps its usable output
files and does not need a decision reply unless the state explicitly says `awaiting-*`.
