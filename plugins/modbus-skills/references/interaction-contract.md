# Fast Interaction Contract

Use this contract in every Modbus skill.

- Continue through safe, local, read-only, deterministic steps without asking for
  permission or pausing after each stage.
- Verify artifact structure, counts, hashes, ranges, identities, and invariants with
  code. Report the result; do not ask the user to confirm what the artifacts prove.
- Review large inputs as one bounded scope. Group exceptions by shared cause or
  required decision. Never default to page-by-page, row-by-row, point-by-point, or
  file-by-file confirmation.
- Ask once, with all necessary context, only when a missing choice materially changes
  the result, low-confidence evidence must be accepted, a live device action is next,
  or an external/native application must be operated.
- Apply a user's clearly scoped confirmation to every item in that scope. Record one
  decision with the source hash, selection, item count, and any exceptions.
- Keep unresolved engineering fields as holds and keep unsupported writes, broadcasts,
  discovery, and unbounded polling stopped. Efficiency never weakens those boundaries.

The default handoff is a completed artifact plus a compact exception list. A long
approval queue is a workflow defect unless each item genuinely requires a different
engineering judgment.
