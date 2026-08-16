# Post: the firmware rev changed and the map no longer matches the site

Status: draft. Do not post until you have reviewed it.

Suggested places: a commissioning post-mortem, SI Slack, LinkedIn, a thread about a device that “moved” registers after an update.

Do not say this is listed in a marketplace unless that listing is live.

---

A firmware revision is the quiet way a good poll plan goes stale. A point keeps the same name and changes offset. A new alarm block appears. A temperature moves from holding to input. The old CSV still imports, so nobody notices until the trend looks wrong.

What I want from a map compare is boring and specific: added, removed, moved, changed. Not a full retype of both manuals.

There is a read-only workflow for that. Review each source so you are comparing two validated maps, then ask for the diff. It treats a name-only change as noise. Route, unit, area, and offset changes are moves.

Repo:

https://github.com/studioxvii/modbus-skills

```text
$compare-maps Compare these two firmware maps and show moved, added,
removed, and changed points.
```

If you are starting from two vendor files rather than two cleaned maps, compile or review each one first. Do not paste a customer map into a public thread.
