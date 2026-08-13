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
4. For an OEM PDF, spreadsheet, JSON, XML, XLSX, or text file, or for broad setup
   help that does not name a specialist, recommend `compile-user-map`. Do not
   replace that outcome with a parse-normalize-plan-builder chain, and do not
   treat Node-RED as the default finish.
5. When source-map review itself is the requested outcome, recommend `review-map`.
6. When a validated map is already in hand and the user named a target, recommend
   that builder or `build-tool-pack`. When a capture is already in hand, recommend
   `analyze-capture` or `check-byte-order`.
7. Read that skill's current `SKILL.md` before describing it.

Do not ask who will run the work, where files belong, or how to structure tests when
the request or repository already answers those questions. Use the current project
and its existing output folders.

Keep OEM and broad-setup routes on `compile-user-map` and explicit-stage routes
direct. Offer one alternate only when the goal is materially ambiguous.

Use this format:

```text
Recommended next: skill-name
Safe path: compile-user-map for OEM or broad setup; omit for an explicit stage request
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

## Stop

- Stop for writes, broadcasts, discovery scans, stored credentials, or unbounded polling.
- Do not perform the recommended skill in this invocation.
- Do not invent a specialist chain when `compile-user-map` completes the goal.
- Do not ask who will run the work or where files belong when the request already answers it.

## Finish

End with the recommendation format above. Do not perform the recommended skill here.
When the user replies `proceed`, that authorizes only the named safe next skill; live-device
and native-app gates remain explicit.
