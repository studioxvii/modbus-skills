# Fast Interaction Contract

Use this contract in every Modbus skill.

- Continue through safe, local, read-only, deterministic steps without asking for
  permission or pausing after each stage.
- Use the runtime's validators, receipts, and case inspector to verify artifact
  structure, counts, hashes, ranges, identities, and invariants. Inspect the requested
  result and holds, but do not duplicate successful built-in checks with scratch
  validators or repeated full-file reads. Add a focused check when a requested
  property is not covered or evidence disagrees. Report what is proved; do not ask
  the user to repeat these checks.
- Review large inputs as one bounded scope. Group exceptions by shared cause or
  required decision. Never default to page-by-page, row-by-row, point-by-point, or
  file-by-file confirmation.
- Ask once, with all necessary context, only when a missing choice materially changes
  the result, low-confidence evidence must be accepted, a live device action is next,
  or an external/native application must be operated.
- When a missing answer blocks the remaining offline work, ask the grouped question
  and end the turn. Do not sleep or poll for the reply, or repeat the unanswered
  question; continue when the reply arrives.
- Do not ask about the actor, file location, output folder, or test structure when the
  request or repository already answers it. Use the repository's existing layout and
  the smallest safe default.
- Apply a user's clearly scoped confirmation to every item in that scope. Record one
  decision with the source hash, selection, item count, and any exceptions.
- Keep unresolved engineering fields as holds and keep unsupported writes, broadcasts,
  discovery, and unbounded polling stopped. Efficiency never weakens those boundaries.
- Use short, plain-English labels in user-facing output. Put schema names, hashes, and
  internal mechanics in the audit artifact unless they change the user's next action.

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
3. When a runtime command returns a receipt, include one structured `next_action` that
   matches the human recommendation. Do not make agents reconstruct the handoff from prose.
4. When the requested outcome is complete, lead with `Done:` and name the artifact.
   Offer no next skill unless another goal is materially useful; label such routes
   `Optional:`.
5. When more work is required, choose exactly one `Recommended next:` skill, or
   `Continue:` the current skill when it owns the remaining work. State why, pass the
   exact artifact paths or case reference it needs, and name what it will produce.
6. Show at most two `Other options:` and only when they represent genuine user-goal
   branches. Never print the full skill catalog or every handoff rule.
7. End an actionable recommendation with `Reply \`proceed\` to continue.` A reply of
   `proceed` authorizes only the named safe next skill. On that reply, the active agent
   must read the named sibling skill's current `SKILL.md` and execute it with the exact
   artifacts named in `Uses:`. Do not rely on host-specific implicit invocation or ask
   the user to type a host command. This continuation does not authorize any other
   skill, live-device action, or native-app operation; those gates remain explicit.

Treat each skill's Handoff section as a routing table, not text to reproduce. Render
only the route that matches the current artifact and goal.

Use this compact shape:

```text
Done: <artifact and outcome>

Recommended next: skill-name
Why: <one sentence>
Uses: <exact current artifact paths or case reference>
Produces: <next artifact or resolved outcome>

Other options: <zero to two concise alternatives>
Reply `proceed` to continue.
```
