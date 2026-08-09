# Verification Status

Verification date: 2026-08-08.

## Passed

- The OpenAI plugin validator accepted the plugin manifest.
- The skill validator accepted all 19 skills.
- The dependency-free repository suite passed 254 tests in the current working tree.
- The last clean-checkout run covered the prior 250-test repository suite; rerun it
  after these changes are committed.
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
150-row synthetic benchmark completed the offline bundle in 14.829 ms on the recorded
macOS arm64 / Python 3.14.6 envelope, below the five-minute local threshold. Wall time
is diagnostic; transcript shape is the deterministic repository gate.

## Not yet verified

Native application tests did not run because Node-RED, the supported Modpoll implementations, Witte Modbus Poll, and ModScan are not installed in the test environment. Generated target manifests correctly report `verification: "not-run"`.

Do not publish the repository until the remaining items in `publication-checklist.md` are complete. The open-source license, publisher contact, final URLs, native target tests, and new-task plugin install are still release gates.
