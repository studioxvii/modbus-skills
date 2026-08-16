# Seed-user script

Ask eight people to run one real job. Do not ask them to “try the plugin.”

## Who to ask

| # | Kind of person | Job to give them |
| --- | --- | --- |
| 1–3 | Commissioning or SI engineers you can text | Compile one vendor PDF or spreadsheet they already have |
| 4–5 | Node-RED users who poll Modbus | Compile a map, then build a disabled Node-RED flow |
| 6–7 | Cursor / Claude / Codex users who touch industrial or IoT work | Same compile job, in the agent they already use |
| 8 | Someone burned by byte order or a firmware map change | `$check-byte-order` on one sample, or `$compare-maps` on two revisions |

If you cannot fill a row, skip it. Eight is a target, not a quota.

## What to send

Keep the ask to five lines:

> I have a read-only plugin that turns a Modbus manual into a user map and stops when the manual is unclear. Can you run one job you already have, not a demo file? Install is in the README. If you get stuck I can sit on a call. I want to know where it stalls.

Attach:

- https://github.com/studioxvii/modbus-skills
- [First 10 minutes](../../README.md#first-10-minutes)
- [When to use](../when-to-use.md)
- The [synthetic example](../examples/compile-user-map/README.md) only if they want a dry run first

Do not send the 20-skill catalog.

## What they should run

Preferred job:

```text
$compile-user-map Use this local manual or spreadsheet to create a user map
for the measurements I actually poll. Return Markdown, JSON, and CSV.
```

Backup jobs, only if compile is the wrong shape:

```text
$check-byte-order Evaluate every supported layout for these raw words.
```

```text
$compare-maps Compare these two firmware maps and show moved, added, removed, and changed points.
```

They must use their own file. The public example is synthetic and will not teach you whether a real manual works.

## What to write down

Use the same notes for every person:

| Field | Note |
| --- | --- |
| Person and role | |
| Agent (Codex / Cursor / Claude / CLI) | |
| Job they ran | |
| Did they finish install without you? | yes / no / where they stopped |
| Did the first prompt make sense? | |
| Did they get `output/` files? | |
| What did it refuse to guess? | |
| Would they use it on the next device? | yes / no / not sure |
| Exact quote of the stall or the win | |

## How to read the results

- 3 of 8 finish a job and say they will use it again: keep going to posts and listings.
- Bounce at install: fix the install path before posting.
- Bounce at “which skill?”: the homepage and `$modbus-help` are not clear enough.
- They wanted it to guess byte order: that is the product working. Confirm they still trust the hold.

Do not count a star, a polite “looks cool,” or a run of the synthetic example as a finished job.
