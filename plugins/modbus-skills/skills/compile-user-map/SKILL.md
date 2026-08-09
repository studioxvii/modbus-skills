---
name: compile-user-map
description: Compile an OEM Modbus PDF or structured register map plus measurement intent into an organized user map, JSON, CSV, and optional target outputs in one resumable run.
---

# Compile User Map

Produce the requested outcome without exposing internal stages.

Follow `../../references/interaction-contract.md`.

## Process

1. Read `references/request.md`.
2. Put the OEM source, measurement intent, optional targets, and any resume input in
   one request file.
3. Run `python3 <skill-dir>/scripts/run.py --request <request.json> --output <case-directory>`.
4. Return the result state, elapsed time, artifact paths, exclusions, and next action.
5. When the result needs a decision, present its complete grouped packet once, encode
   the reply in a new request, and rerun the same case. Do not invent fields or bypass
   case, source, packet, or artifact hashes.

Completion requires `compile-result.json` plus the complete offline user-map bundle.
Requested targets may remain independently held without invalidating completed outputs.

Never perform a live device read. Return a case-bound probe when physical evidence is
required. Keep unsupported writes, broadcasts, scans, credentials, and polling stopped.
