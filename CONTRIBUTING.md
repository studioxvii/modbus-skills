# Contributing

Modbus Skills accepts focused, read-only improvements.

## Contribution license

Unless you explicitly state otherwise, any contribution intentionally submitted for
inclusion in this project is licensed under the Apache License, Version 2.0, without
additional terms or conditions, as described in section 5 of [`LICENSE`](LICENSE).
You must have the right to submit the contribution.

## Before you change code

- Start with a real Modbus engineering problem and a primary source.
- Add or update one focused skill when possible.
- Keep deterministic calculations and file generation in the runtime.
- Use only synthetic or clearly redistributable fixtures.
- Do not copy vendor manuals, vendor maps, customer data, or private product code.
- Do not add write functions, broadcast requests, discovery scans, or unbounded polling.

## Skill requirements

Each skill must have:

- `license: Apache-2.0` in its `SKILL.md` frontmatter.
- A precise activation description with both what it does and a `Use when` clause.
- A short workflow that follows the shared interaction contract.
- Explicit inputs, outputs, and one observable completion criterion.
- A `Handoff` section for operational skills, or a `Finish` section for routers/outcomes.
- Blocking conditions.
- At least ten positive activation cases.
- At least five close-negative activation cases.
- Deterministic tests when the skill performs calculations or generates files.

## Verify

Run:

```bash
python3 scripts/verify_repo.py
```

For a new target adapter, also test the artifact in the named application before you mark native verification complete.
