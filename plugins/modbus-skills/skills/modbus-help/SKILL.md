---
name: modbus-help
description: Choose the next Modbus skill or short workflow from the user's goal and current artifact.
---

# Modbus Help

Route, verify, stop. This skill recommends work; it does not perform it.

Follow `../../references/interaction-contract.md`.

## Process

1. Read `../../references/user-paths.md`.
2. Identify the user's goal and the artifact they already have.
3. Route OEM-source-to-organized-map or output requests to `$compile-user-map`.
   Select a specialist only for an explicitly requested stage, comparison, remap,
   capture, byte-order check, read plan, or target-only build.
4. Read that skill's current `SKILL.md` before describing its behavior or inputs.
5. Reply with the next skill, why it fits, what it needs, and what it produces.

Do not ask who will run the work, where files belong, or how to structure tests when
the request or repository already answers those questions. Use the current project
and its existing output folders.

Show a sequence only when the user asks for the full path. Keep it to the shortest useful path. Offer one alternate only when the goal is materially ambiguous.

Use this format:

```text
Recommended next: $skill-name
Why: one sentence
Uses: exact current artifact paths or missing decision
Produces: next artifact
Other options: zero or one alternative when materially useful
Reply `proceed` to continue.
```

For writes, broadcasts, discovery, or unbounded polling, explain the read-only boundary and stop.

## Output files

- None. This skill only names the next skill, the input it needs, and the result it will produce.

Completion requires one verified route with its required input and observable output.
