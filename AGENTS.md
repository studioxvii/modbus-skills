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

## Cloud Agent environment

- Repo-managed config lives in `.cursor/environment.json`.
- Install is `python3 -m pip install -e .` (Python 3.11+; no secrets or long-running server).
- There is no `start` command. Prove the environment with `python3 scripts/verify_repo.py`.

## Verification

- Run `python3 scripts/verify_repo.py` before handoff.
- Add unit tests for each deterministic change.
- Add positive, negative, incomplete-input, and unsafe-request cases for each skill.
- Keep generated output deterministic.

## Cursor Cloud specific instructions

- This is a Python-only, dependency-light CLI project (single runtime dependency `pdfplumber`). There is no server, database, or long-running process; the "application" is the `modbus_skills` CLI run via `python3 plugins/modbus-skills/scripts/modbus_skills.py <command> ...` (or the installed `modbus_skills` package). See `README.md` and `plugins/modbus-skills/runtime/modbus_skills/cli.py` for the full command list.
- Known environment caveat: on the Cloud Agent VM the filesystem timestamp granularity is coarse (~4 ms, overlayfs/`/tmp`). The single test `tests/test_node_red_live_campaign.py::...test_admin_driver_deploys_once_for_multiple_rounds_and_restores_once` deterministically fails here with `CampaignError: Node-RED read plan did not drain before the timeout` because it detects a fresh capture via an mtime change between two rapid identical writes that collapse to the same mtime. This is not a code regression: GitHub CI (`ubuntu-latest`, ext4 with nanosecond mtime) passes `verify_repo.py`, and the other 462 tests pass here. Do not "fix" it by editing runtime code for this env; treat that one failure as an expected VM artifact.
