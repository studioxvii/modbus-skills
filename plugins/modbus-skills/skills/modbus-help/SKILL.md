---
name: modbus-help
description: Choose the next Modbus skill or short workflow from the user's goal and current artifact. Use when the user is unsure which Modbus skill to run, asks what to do next, or needs a safe path from their current artifact.
license: Apache-2.0
---

# Modbus Help

Route, verify, stop. This skill recommends work; it does not perform it.

Follow `../../references/interaction-contract.md`.

## Process

1. Read `../../references/user-paths.md`.
2. Identify the user's goal and the artifact they already have.
3. Route an explicitly requested stage directly to its specialist.
4. For broad setup, validation, or troubleshooting requests, recommend this complete
   safe path automatically:
   `normalize-map -> check-map -> plan-reads -> build-node-red -> capture-sample -> analyze-capture -> check-byte-order`.
   Add `parse-map` or `extract-pdf-map` first when the source is raw.
5. Route an OEM-source-to-organized-output request to `compile-user-map` when the
   user wants compilation rather than the live evidence path.
6. Read that skill's current `SKILL.md` before describing it. For a broad path,
   repeat this check for each recommended skill.
7. Reply with the path, the immediate next skill, its input, and its output.

Do not ask who will run the work, where files belong, or how to structure tests when
the request or repository already answers those questions. Use the current project
and its existing output folders.

Keep broad paths complete and explicit-stage routes direct. Offer one alternate only
when the goal is materially ambiguous.

Use this format:

```text
Recommended next: skill-name
Safe path: full chain for broad requests; omit for an explicit stage request
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

## Finish

End with the recommendation format above. Do not perform the recommended skill here.
When the user replies `proceed`, that authorizes only the named safe next skill; live-device
and native-app gates remain explicit.

