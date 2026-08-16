# Verification Status

Verification date: 2026-08-15.

## Node-RED live campaign

Native acceptance was exercised locally on 2026-08-09 with Node-RED 5.0.4,
Node.js 25.8.0, and `node-red-contrib-modbus` 5.60.1 against the clean
`generator-fleet-simulator` checkout at commit `39d20e9`.

- The 10-unit pass produced 10/10 unit-specific FC3 responses through the
  generated manual flow.
- The 50-unit bounded pass produced 59 sequential requests covering all 50 unit
  IDs before the 60-second campaign cap; the simulator queue drained to 0 and
  the returned raw register values matched the REST register oracle.
- The simulator-side `fault-and-reset` scenario changed Unit 1 alarm word 1 at
  holding-register offset 13 from 5 to 20. Node-RED read back 20 during the
  fault and 5 after restoration, with no Node-RED writes.
- A stopped-simulator request surfaced Modbus failures; after restart and
  readiness, the next bounded Node-RED read recovered successfully.

The original live flow was imported disabled, reviewed, then enabled with temporary
test instrumentation. The exporter now includes a bounded `capture/v1` output and
the campaign runner can operate the single start button through the local Node-RED
API. That new automated path has deterministic tests but has not replaced the
recorded manual native evidence above.

## Passed

- The OpenAI plugin validator accepted the plugin manifest.
- The skill validator accepted all 20 skills.
- The dependency-free repository suite passed 449 tests, then the deterministic skill-usability campaign passed, in `python3 scripts/verify_repo.py`.
- The public synthetic human workflow passed 41 of 41 checks.
- A local rights-restricted corpus of seven real register maps passed 45 of 45 workflow checks. The maps and local artifacts are not in this repository.
- Three blind users completed the novice probe, commissioning-pack, and map-review scenarios without repository edits or live device traffic.
- The public-boundary scan passed after the local corpus was removed from the repository tree.

The real-map workflow confirmed these behaviors:

- Unknown routes, unit IDs, areas, address conventions, access states, duplicate generated IDs, and byte orders stay as holds.
- A source review flag remains evidence. It does not approve the canonical map.
- Write-only points do not enter read plans.
- Final output rejects a missing, malformed, or stale map hash in the read plan.
- One raw sample produces all byte-order candidates without added Modbus reads.
- Node-RED probe flows use manual one-shot reads. Final flows use one bounded five-second live-poll trigger that keeps one request in flight. Both are disabled by default and contain no deploy-time or write nodes.
- Node-RED, Modpoll (BETA), and ModScan (BETA) can be built alone or in any combination.
- Tool packs are deterministic, checksummed, read-only, and free of review audit data.
- JSON and CSV capture analysis reports communication, duplicate, gap, stale, flatline, range, rate, and byte-order evidence conditions.

The outcome-compiler transcript suite also passed clean structured intake, automatic
PDF coordinate fallback, one grouped selection resume, preserved offline artifacts at
the binding gate, and one evidenced read across offsets 257 through 308. Its tracked
150-row synthetic benchmark completed the offline bundle in under 20 ms on the recorded
macOS arm64 / Python 3.14.6 envelope, below the five-minute local threshold. Wall time
is diagnostic; transcript shape is the deterministic repository gate.

## BETA targets

Modpoll, Witte Modbus Poll, and ModScan are shipped as BETA. They were not
installed and were not run. Generated target manifests still report
`verification: "not-run"` for those targets. Operator files label the same
status as BETA. The Node-RED native result above is a separate local acceptance
record and does not replace the deterministic repository checks.

## Public install proof

Recorded 2026-08-15 from a fresh clone of the public repository.

- `codex plugin marketplace upgrade modbus-skills` refreshed the Git marketplace
  snapshot to `a32a576`.
- `codex plugin add modbus-skills@modbus-skills` installed version `0.2.0`.
- `python3 -m pip install -e .` in a clean virtualenv failed until package
  discovery was pinned to `plugins/modbus-skills/runtime`. After that fix, the
  runtime imported and `pdfplumber` 0.11.10 installed.
- `$compile-user-map` on the local E50B1 CX PDF completed in 1.5 s with
  `awaiting-source-decision`. Extraction found no register rows and asked for a
  corrected source instead of guessing.
- The same public checkout compiled
  `tests/fixtures/maps/synthetic_registers.csv` to `offline-complete` and wrote
  `output/user-map.md`, `output/user-map.json`, and `output/user-map.csv`.
