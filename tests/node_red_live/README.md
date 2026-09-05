# Node-RED live campaign

This is a small human-level campaign for a Codex agent using the Generator Fleet
Simulator as a local Modbus TCP oracle. It complements deterministic flow tests and
does not replace them.

## What the agent runs

The same read-only journey is run at exactly two profiles:

- **10 units:** unit identifiers 1–10.
- **50 units:** unit identifiers 1–50.

For each profile, the agent checks simulator readiness, binds the canonical-map,
read-plan, flow, manifest, and simulator-configuration SHA-256 values, imports the
flow while disabled, and records scoped authorization for this named local
campaign. Explicit standing user scope may authorize an unattended synthetic run;
record its actor as `test-harness`, not as a human approval. The authorization
covers the campaign; it is not repeated for each read.

The correctness pass triggers compiled blocks manually. The bounded stress pass uses
three rounds (sequential), a one-second cadence, one read in flight, at most 180
compiled-block reads, and a 60-second wall-clock cap. If a request would exceed a
bound, stop and report the bound rather than silently extending it.

The capture must use `capture/v1`. Every row keeps route, unit identifier, register
area, protocol offset, timestamp, raw words, derived values, response time, and a
success or error state. A communication error remains an error and has no derived
sample. The existing capture analysis workflow is the next human-reviewable step.

## Safety and stop rules

The target must be loopback (`127.0.0.1`, `localhost`, or `::1`) and the flow must
permit only FC01–FC04 reads. Do not proceed when the endpoint is non-local, a hash is
missing or stale, the simulator is not ready, the required Node-RED contrib node is
unavailable, or the imported flow contains writes, broadcasts, discovery scans,
deploy-time triggers, credentials, or scheduled polling. Node-RED never writes
command register 20.

On timeout or an invalid response, keep the error row and continue the bounded
plan. Do not retry in a hidden loop. If a second attempt is useful, start a new
named run so the evidence stays clear.

## Evidence a reviewer receives

Each run has a run ID and a sanitized receipt containing profile, fleet size, status,
terminal state, hashes, versions, request and error counts, latency, queue/readiness
signals, capture and analyzer artifact types, issue codes, and cleanup status. Valid
terminal statuses are `passed`, `failed`, `blocked`, `not-run`, and `inconclusive`.

Raw captures, Node-RED logs, simulator snapshots, and transcripts belong only below
ignored `artifacts/` or `private/` output. Tracked files must not include simulator
exports, credentials, private captures, absolute local paths, or full agent prose.

## Run it

Start the simulator and a disposable local Node-RED runtime first. Generate the
Node-RED flow from the reviewed map and read plan. Then run:

```text
python3 scripts/run_node_red_live_campaign.py \
  --profile fleet-10 \
  --authorize \
  --node-red-cli /path/to/node-red \
  --flow /path/to/node-red/flow.json \
  --canonical-map /path/to/map.json \
  --read-plan /path/to/read-plan.json \
  --manifest /path/to/node-red/manifest.json \
  --simulator-config /path/to/simulator-config.json \
  --capture ./private/output/capture.json \
  --hashes ./private/output/hashes.json
```

These five evidence files let the runner prove it is using the map, read plan,
flow, Node-RED manifest, and simulator setup you reviewed. The hashes file must
contain the SHA-256 value for each file. A mismatch stops the run before any
Modbus request.

The runner imports the reviewed flow, enables it for this run, clicks its single
start button through the local Node-RED API for three short rounds, waits for
every planned request,
checks raw words against the simulator API, and restores the original Node-RED
flows. If any required input is missing, it returns `blocked` or `not-run` instead
of claiming a live pass.

Endpoint placeholders on Modbus client configuration nodes are resolved directly
in the disposable deployment copy from the verified loopback simulator binding.
This includes global configuration nodes, which cannot see flow-tab environment
variables. No process-level `MODBUS_*_HOST` or `MODBUS_*_PORT` variables are needed.
Missing or unsafe bindings stop before deployment. The generated evidence files
remain unchanged, and the original runtime flows are restored on exit, including
when a trigger or capture fails.
