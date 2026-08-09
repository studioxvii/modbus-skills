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

## Completion and next step

End every skill run with guidance derived from the user's requested outcome and the
artifacts just produced:

1. Continue safe deterministic work owned by the current skill automatically. Do not
   expose its internal stages as a skill chain.
2. Inspect artifact holds, target statuses, and missing inputs before declaring the
   outcome done. A runtime `next_action: none` value alone is not proof that the user's
   outcome is usable.
3. When the requested outcome is complete, lead with `Done:` and name the artifact.
   Offer no next skill unless another goal is materially useful; label such routes
   `Optional:`.
4. When more work is required, choose exactly one `Recommended next:` skill, or
   `Continue:` the current skill when it owns the remaining work. State why, pass the
   exact artifact paths or case reference it needs, and name what it will produce.
5. Show at most two `Other options:` and only when they represent genuine user-goal
   branches. Never print the full skill catalog or every handoff rule.
6. End an actionable recommendation with `Reply \`proceed\` to continue.` A reply of
   `proceed` authorizes only the named safe next skill; live-device and native-app gates
   remain explicit.

Treat each skill's Handoff section as a routing table, not text to reproduce. Render
only the route that matches the current artifact and goal.

Use this compact shape:

```text
Done: <artifact and outcome>

Recommended next: $skill-name
Why: <one sentence>
Uses: <exact current artifact paths or case reference>
Produces: <next artifact or resolved outcome>

Other options: <zero to two concise alternatives>
Reply `proceed` to continue.
```
