# Modbus Skills Repository Guidance

## Purpose

Build public, read-only Modbus engineering skills and deterministic workflows.

## Public Boundary

- Treat every tracked file as future public material.
- Do not copy files, fixtures, text, identifiers, prompts, or history from a private product repository.
- Do not add vendor manuals or complete vendor register maps without documented redistribution rights.
- Use synthetic fixtures by default.
- Do not add customer names, hostnames, IP addresses, credentials, local absolute paths, or internal service identifiers.

## Safety

- Support Modbus reads only. Permit function codes 01, 02, 03, and 04.
- Do not generate write requests, broadcast requests, discovery scans, or unbounded polling.
- Keep unresolved address conventions, register areas, unit identifiers, datatypes, and byte orders visible as holds.
- Require one scoped human confirmation before live device use or when unresolved
  engineering evidence could materially change a final artifact.

## Human-Time Budget

- Optimize for minutes of human attention, not maximum approval ceremony.
- Complete safe, local, deterministic work without intermediate check-ins.
- Verify what code and artifacts can prove; never ask a person to repeat that work.
- Group unresolved items by root cause and ask for all necessary choices together.
- Never default to page-by-page, row-by-row, point-by-point, or file-by-file approval.
- Treat a user's clearly scoped confirmation as applying to the whole stated scope and
  record it once with hashes, counts, and exceptions.

## Implementation

- Use Python 3.11 or later and the standard library unless a dependency is approved.
- Put deterministic behavior in `plugins/modbus-skills/runtime/modbus_skills/`.
- Keep each `SKILL.md` concise. Put detailed contracts and target notes in `references/`.
- Write each skill description with both what it does and a `Use when` activation clause.
- Preserve canonical artifact contracts across skills.
- Use composite point identity: route, unit identifier, register area, protocol offset, and logical point identifier.

## Verification

- Run `python3 scripts/verify_repo.py` before handoff.
- Add unit tests for each deterministic change.
- Add positive, negative, incomplete-input, and unsafe-request cases for each skill.
- Keep generated output deterministic.
