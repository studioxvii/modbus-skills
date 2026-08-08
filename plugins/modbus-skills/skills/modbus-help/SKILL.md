---
name: modbus-help
description: Choose the next Modbus skill or short workflow from the user's goal and current artifact.
---

# Modbus Help

Route, verify, stop. This skill recommends work; it does not perform it.

## Process

1. Read `../../references/user-paths.md`.
2. Identify the user's goal and the artifact they already have.
3. Select one next skill from the closest path.
4. Read that skill's current `SKILL.md` before describing its behavior or inputs.
5. Reply with the next skill, why it fits, what it needs, and what it produces.

Show a sequence only when the user asks for the full path. Keep it to the shortest useful path. Offer one alternate only when the goal is materially ambiguous.

Use this format:

```text
Next: $skill-name
Why: one sentence
Needs: current input or missing decision
Produces: next artifact
Then: optional next step
```

For writes, broadcasts, discovery, or unbounded polling, explain the read-only boundary and stop.
