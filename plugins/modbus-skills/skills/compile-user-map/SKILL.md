---
name: compile-user-map
description: Compile an OEM Modbus PDF or structured register map plus measurement intent into an organized user map, JSON, CSV, and optional target outputs in one resumable run.
---

# Compile User Map

Produce the requested outcome without exposing internal stages.

Follow `../../references/interaction-contract.md`.

## Process

1. Read `references/request.md`.
2. For PDF input, locate the bundled workspace dependency runtime and use its Python
   executable when it provides `pdfplumber`. Do not install dependencies during the
   workflow. For structured input, the active Python 3.11+ runtime is sufficient.
3. Put the OEM source, measurement intent, optional targets, and any resume input in
   one request file.
4. Run `<selected-python> <skill-dir>/scripts/run.py --request <request.json> --output <case-directory>`.
5. Inspect the result plus the user-map holds and target statuses against the user's
   requested outcome. `offline-complete` is valid only when source coverage is complete
   and selected points have no blocking holds. Return the state, elapsed time, artifact
   paths, exclusions, and one useful next step; never treat `next_action: none` alone as proof of completion.
6. When the result needs a decision, present its complete grouped packet once, encode
   the reply in a new request, and rerun the same case. Do not invent fields or bypass
   case, source, packet, or artifact hashes.
7. Continue this skill automatically for safe internal stages. When a typed decision,
   physical read, or target choice is required, recommend continuing this skill with
   the exact case and input. Do not expose internal specialist-stage choreography.

## Output files

Open these first:

- `output/user-map.md` - The short human-readable map organized by measurement group.
- `output/user-map.csv` - The spreadsheet-ready map for people and common tools.
- `output/user-map.json` - The same map in the complete machine-readable format.

Normally leave these alone:

- `targets/` - Open this folder only when you requested files for Node-RED, Modpoll, or ModScan. It contains the files to import into that tool.
- `compile-result.json` - Codex reads this receipt to tell whether the run finished or needs something from you. You normally do not need to open it.
- `case.json` - This checkpoint lets Codex continue the same job later without starting over. Keep it until the job is finished, and do not edit it.
- `artifacts/` and `control/` - These let Codex verify and resume the job. Keep them for troubleshooting; ignore them during normal use.

Completion requires `compile-result.json` plus the complete offline user-map bundle.
Requested targets may remain independently held without invalidating completed outputs.

## Finish

- The requested offline bundle is usable: say `Done`, name its human, JSON, and CSV
  artifacts, then offer target generation only as an optional goal.
- The current case can advance safely: continue it without a handoff.
- A decision or external action blocks the requested outcome: recommend continuing
  `$compile-user-map` with the case-bound packet or probe and invite `proceed`.
- A requested target is held while the offline map is usable: report both facts and
  recommend the one action that unlocks that target.

Never perform a live device read. Return a case-bound probe when physical evidence is
required. Keep unsupported writes, broadcasts, scans, credentials, and polling stopped.
