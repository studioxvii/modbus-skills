# Verification Status

Verification date: 2026-08-08.

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
- The dependency-free repository suite passed 308 tests in the current working tree.
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
- Node-RED probe and final flows use manual one-shot reads. They are disabled by default and contain no scheduled or write nodes.
- Node-RED, Modpoll, and ModScan can be built alone or in any combination.
- Tool packs are deterministic, checksummed, read-only, and free of review audit data.
- JSON and CSV capture analysis reports communication, duplicate, gap, stale, flatline, range, rate, and byte-order evidence conditions.

The outcome-compiler transcript suite also passed clean structured intake, automatic
PDF coordinate fallback, one grouped selection resume, preserved offline artifacts at
the binding gate, and one evidenced read across offsets 257 through 308. Its tracked
150-row synthetic benchmark completed the offline bundle in under 20 ms on the recorded
macOS arm64 / Python 3.14.6 envelope, below the five-minute local threshold. Wall time
is diagnostic; transcript shape is the deterministic repository gate.

## Not yet verified

Modpoll, Witte Modbus Poll, and ModScan were not installed and were not run.
Generated target manifests still correctly report `verification: "not-run"` for
those targets. The Node-RED native result above is a separate local acceptance
record and does not replace the deterministic repository checks.

Do not publish the repository until the remaining items in `publication-checklist.md` are complete. The open-source license, publisher contact, final URLs, native target tests, and new-task plugin install are still release gates.
