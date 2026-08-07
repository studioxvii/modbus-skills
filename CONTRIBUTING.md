# Contributing

Modbus Skills accepts focused, read-only improvements.

## Before you change code

- Start with a real Modbus engineering problem and a primary source.
- Add or update one focused skill when possible.
- Keep deterministic calculations and file generation in the runtime.
- Use only synthetic or clearly redistributable fixtures.
- Do not copy vendor manuals, vendor maps, customer data, or private product code.
- Do not add write functions, broadcast requests, discovery scans, or unbounded polling.

## Skill requirements

Each skill must have:

- A precise activation description.
- A short workflow.
- Explicit inputs and outputs.
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
