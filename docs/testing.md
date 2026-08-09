# Test Strategy

## Fast tests

- Address conversion and boundary tests.
- Composite identity tests.
- Byte-order vectors and IEEE edge cases.
- Parser round trips and rejected-row diagnostics.
- Map validation and comparison, including moved points.
- Review-decision validation, audit data, exclusions, and approval holds.
- Read-plan grouping, stale-plan rejection, and target limits.
- Adapter determinism and read-only safety.
- All seven non-empty target combinations.
- Skill metadata, activation fixtures, and workflow contracts.

## Public fixtures

Tracked fixtures are synthetic. They cover all four Modbus data areas, multiple unit identifiers, address boundaries, common datatypes, declared byte layouts, gaps, overlaps, bits, strings, counters, and error cases.

Do not add a vendor manual, a complete vendor register map, customer data, or a private product artifact to this repository. A non-synthetic fixture needs a documented redistribution right before it can be tracked.

## Outcome compiler transcripts

Run the deterministic human-time contract with:

```bash
python3 scripts/run_compile_workflow_tests.py \
  --output /tmp/modbus-compile-workflow
```

The suite covers clean structured intake, automatic PDF coordinate fallback, one
grouped selection decision and resume, preserved offline artifacts across a binding
gate, and minimum safe reads inside an evidenced readable island. It fails on page or
row approval loops, dependency installation, stage-skill handoffs, repeated holds, or
more than one source-phase decision packet.

The optional local timing profile uses the tracked 150-row synthetic fixture:

```bash
python3 scripts/run_compile_workflow_tests.py \
  --output /tmp/modbus-compile-benchmark \
  --benchmark
```

Normal CI asserts transcript shape rather than wall time. The benchmark separately
records its fixture hash, machine and Python versions, elapsed time, and selected-point
count, and applies the five-minute offline threshold.

## Human workflow tests with local maps

Run a separate, local-only corpus when testing against real register maps. Keep that corpus outside the repository and out of source control. The corpus owner must have the right to use each source for local test work.

The human test harness accepts a caller-supplied corpus directory. It must not copy source maps into tracked output. Its public report contains only generic case identifiers, counts, issue codes, and pass or fail state.

Run it with an external corpus and a new external output directory:

```bash
python3 scripts/run_human_workflow_tests.py \
  --corpus-dir /path/to/local-corpus \
  --output /path/to/local-output
```

Repository-local output is allowed only below `artifacts/` or `private/`.
The runner verifies that Git ignores the selected path before it writes files.
It rejects output below other repository paths, such as `site/` or `docs/`.
This prevents a local map artifact from entering a public commit by mistake.

For example, this output stays in the ignored `artifacts/` directory:

```bash
python3 scripts/run_human_workflow_tests.py \
  --corpus-dir /path/to/local-corpus \
  --output artifacts/local-human-workflow
```

The corpus needs `corpus.json`. Assign the roles `clean`, `byte_order`, `safety`, `compare_before`, and `compare_after` in a `workflow_cases` object. You can instead use those values in each map entry's `role` field. Add `expected_point_count` when row preservation must be exact.

Test each map as an engineer would use the skills:

1. Diagnose the source without defaults. Confirm that unknown fields become holds and that source rows remain traceable.
2. Apply only explicit local defaults. Confirm areas, address bases, units, datatypes, access, and write-only points are not guessed.
3. Confirm source `include` and `reviewed` flags remain visible. A source review flag must not approve the canonical map.
4. Compile a read plan. Confirm it includes read function codes only and excludes write-only points.
5. Generate a final multi-target pack only when all required fields are reviewed. Confirm every selected target is present and read-only.
6. For an unresolved 32- or 64-bit byte order, generate a bounded probe first. Capture one raw sample with the point's actual word width, datatype, route, unit, area, and protocol-offset identity.
7. Evaluate the matching 32- or 64-bit datatype family and every supported layout from that immutable sample. Confirm the candidate count matches the width and that the evidence does not select a winner.
8. Record a human decision with a reason and the exact evidence reference. Select a layout that exists for the sampled point identity and datatype. Apply it to a new map, rebuild the plan, and then generate the final pack.
9. Modify a map revision by row order and by point location. Confirm row order does not cause a false diff and a location change reports as moved.
10. Analyze bounded JSON and CSV captures. Confirm errors, stale values, flat values, range conditions, duplicates, and raw-word ambiguity are visible.
11. Inspect generated files as a user would. Confirm names, setup steps, checksums, holds, and stop conditions are clear.

## Blind forward tests

Use a fresh agent with only a task prompt and a permitted local input. Do not provide expected answers or internal implementation details.

Test at least these roles:

- A new user who wants a safe probe from an incomplete map.
- A commissioning engineer who needs one or more read-only target artifacts.
- A reviewer who compares map revisions and explains holds.

Pass only when the agent chooses the shortest safe skill chain, preserves genuine
holds, batches human confirmation only where required, creates no write request, and
names the output artifacts and remaining limits clearly. Page-by-page, row-by-row, or
point-by-point confirmation fails unless each unit requires a distinct decision.

## Native acceptance and limits

Static output checks prove deterministic generation. They do not prove that every target application accepts an artifact or communicates with a device.

Before release, complete these tests with pinned tool versions and a synthetic Modbus server:

- Import the Node-RED flow with the supported Modbus node version.
- Load the open-source Modpoll profile with the supported implementation.
- Create and reopen desktop polling artifacts in the licensed application.
- Load ModScan artifacts in the licensed application.
- Compare each target read with the same known synthetic response.

If a native application or license is unavailable, record that result as a release blocker. Do not represent static checks as native-tool proof.
