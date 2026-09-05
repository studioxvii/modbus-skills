---
name: build-modpoll
description: Build deterministic read-only BETA artifacts for gavinying/modpoll or supported Witte Modbus Poll profiles. Use when the user asks for Modpoll, Witte Modbus Poll, gavinying CSV, or a Modpoll probe/final setup.
license: Apache-2.0
---

# Build Modpoll (BETA)

Generate artifacts for one explicit Modpoll product profile. This target remains
BETA; generation does not verify the user's exact native application or artifact.

Follow `../../references/interaction-contract.md`.

## Process

1. Read `references/profiles.md` and select `gavinying-cli`, `proconx-cli`, `witte-desktop`, or `witte-v12-xml`.
2. Require a canonical map, read plan, profile, and `probe` or `final` mode.
3. Run `python3 <skill-dir>/scripts/run.py --map <map.json> --plan <read-plan.json> --profile <profile> --mode <mode> --output <directory>`.
4. Inspect the setup manifest, decision report, polling limits, and generated files.
   Gavinying final commands now invoke a source-bound launcher using documented
   native JSON export. Its validated status-and-values envelope is primary;
   native console floats are approximate. The secure launcher requires POSIX
   directory-fd/O_NOFOLLOW support and fails before connecting otherwise.
   Witte final support is limited to desktop identity `uint16` with an explicit
   unsigned display. XML final decoding and unsupported desktop semantics remain
   held; offer an explicitly raw `probe`, never silently substitute raw values.
5. Verify with the native target when available. Otherwise report native verification
   as unavailable and use the fallback only for one reviewed request.

## Output files

- The profile folder under `modpoll/` - Start here. It contains the CSV, XML, or command files used to configure the selected Modpoll product.
- The profile `README.md` - Short operator instructions for those files.
- Gavinying `<route>-read-final.py` - Bounded native launcher; one owned output
  directory retains a single atomic `result.json` status-and-values envelope.
  Stdout is a compact invocation receipt; values and bounded diagnostics stay in
  the envelope. Require successful exit and `published=true`, and match receipt
  `run_id`, `binding_sha256` and `succeeded` status to the envelope with
  `values_current=true`. Preflight/lock failures can leave a prior file untouched;
  its flag alone is not freshness proof. Native
  exit0, rounded stdout, null/stale JSON or a retained prior file is not success.
- `pymodbus-read-once.py` - Optional cross-platform FC01-04 fallback. It requires
  one compiled request, endpoint, port, and matching unit ID.
- `manifest.json` and `modpoll-result.json` - Normally ignore these. They bind the generated files to the exact map and read plan and record any holds.

Completion requires a generated or held status with visible native-verification state.

## Stop

- Stop without a canonical map, read plan, profile, and probe or final mode.
- Never claim native verification when it was not run.
- Stop for write requests or undocumented binary formats.

## Handoff

- No read plan exists: suggest `plan-reads`.
- The user also needs Node-RED or ModScan: suggest `build-tool-pack`.

Preserve native files. Never claim native verification when it was not run.
