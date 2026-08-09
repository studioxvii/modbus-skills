# Codex task: run the local Node-RED campaign

You are the commissioning engineer for this bounded test. Use only the permitted
local Generator Fleet Simulator and the reviewed map/read-plan inputs. Work from the
campaign contract in `fixtures/campaign.json` and this task; do not invent a larger
load test.

1. Preflight the simulator, Node-RED runtime, required contrib node, loopback
   endpoint, and exact SHA-256 bindings. If any prerequisite is absent or stale,
   write a sanitized `blocked` or `not-run` receipt and stop before traffic.
2. Obtain one scoped human authorization for this named local campaign. Do not ask
   for per-read approvals.
3. Import the generated flow while disabled. Verify that it contains only FC01–FC04
   reads, manual one-shot triggers, no writes, no discovery, no deploy-time trigger,
   no scheduled polling, and at most one read in flight. Reject the run if any check
   fails.
4. Run the correctness pass at exactly 10 units and exactly 50 units. Compare each
   response with the same-window simulator register oracle. Preserve complete
   `capture/v1` identity and raw-word evidence.
5. Run three rounds (sequential) per profile with a one-second cadence, at most 180
   compiled-block reads, and a 60-second cap. Record request count, latency,
   readiness, queue drain, and errors.
6. Run the documented simulator-side `fault-and-reset` scenario out-of-band. Read
   the affected unit through Node-RED and prove the state/alarm transition before
   restoration. Never write command register 20.
7. In the 10-unit profile, exercise one controlled endpoint outage or simulator
   restart. Record a timeout/error without a stale derived value, retry once, and
   report recovery or a terminal `blocked` state with cleanup evidence.
8. Submit the capture to the existing analysis workflow. Keep communication errors
   as errors and retain unresolved identity or byte-order holds.

Finish with one sanitized receipt per profile. It must distinguish `passed`,
`failed`, `blocked`, `not-run`, and `inconclusive`, name artifact types and issue
codes, include hashes and versions, and report cleanup. Keep raw captures and logs
under ignored `artifacts/` or `private/` output. Do not commit simulator exports,
credentials, private paths, or full transcripts.
