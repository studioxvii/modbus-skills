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

For CSV, JSON, or XLSX, the command is
`python3 <plugin-dir>/skills/parse-map/scripts/run.py --input <source> --output <inspection.json>`.
The documented request below is sufficient for supported input: inspecting runtime
implementation files or recomputing every completed artifact hash is unnecessary.
The compiler validates requests; the case inspector validates persisted artifacts.

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

Numeric-looking register columns alone do not establish the register area,
zero/one-based convention, or byte layout. Keep those fields unresolved unless
the source documents them or a scoped, explicit `source.defaults` confirms them.
Do not fill a layout merely to make the offline result look complete.

## Resume

Include the existing case reference and exactly one typed input requested by its
`next_action`: a selection decision, binding, immutable capture, or byte-order
decision. Copy the expected case, phase, input, and packet hashes exactly. A plain-
language reply may be translated into the offered candidate shape, but deterministic
runtime validation remains authoritative.

First run
`python3 <skill-dir>/scripts/inspect_case.py <case-directory>`.
If it returns `status: error`, preserve the case and its outputs and report the
integrity problem. Do not repair its state or hashes, silently start over, or use
stale files as completed evidence. A valid result provides `case_id`, `case_hash`,
`next_action`, and `active_packet` for the exact current checkpoint.

For a selection reply, construct the following JSON using that valid inspection.
Copy packet bindings verbatim; replace the decision's selected IDs with only the
offered IDs that the user actually chose. `reason` describes that choice and
`evidence_refs` uses the packet's supplied references. This is a shape example,
not permission to select any point:

```json
{
  "schema_version": "modbus-compile-resume/v1",
  "case_id": "<inspection.case_id>",
  "case_hash": "<inspection.case_hash>",
  "action": "provide-selection-decision",
  "decision_candidate": {
    "schema_version": "modbus-compiler-decision-candidate/v1",
    "case_id": "<packet.case_id>",
    "phase": "<packet.phase>",
    "packet_id": "<packet.packet_id>",
    "source_hash": "<packet.source_hash>",
    "input_hashes": {"<copy every key>": "<copy its hash>"},
    "decisions": [{
      "decision_id": "<offered decision_id>",
      "disposition": "include-specified",
      "selected_subject_ids": ["<user-chosen offered subject_id>"],
      "reason": "<actual user choice>",
      "evidence_refs": ["<offered evidence reference>"]
    }]
  }
}
```

Run `python3 <skill-dir>/scripts/run.py --case <case-directory> --resume <reply.json>`.
Do not replay source parsing on resume. Inspect the resulting receipt and user-map
bundle; the compiler validates the decision and the indexed input artifacts.

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
