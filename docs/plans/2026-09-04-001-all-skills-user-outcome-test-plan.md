# Every-skill Modbus quality and efficiency test plan

Date: 2026-09-04

Status: Execution started 2026-09-04. Unattended testing, documented PRs, automatic merge after passing tests, and final-only human review.

Scope: All 20 shipped Modbus skills, every local working map, and the additional inputs needed to exercise review, comparison, capture, and export skills.

### Execution update: streamline without changing acceptance

The user's subsequent efficiency instruction governs scheduling: freeze this original scope, stop optional investigations and new acceptance requirements, and prioritize remaining coverage gaps and demonstrated user-facing defects. Improve test infrastructure only when it blocks a required test or produces unreliable results. Batch related fixes into fewer PRs; during development run focused regressions and only affected benchmarks. Run the full suite, complete applicable benchmarks and exact-head CI on the final PR candidate before merging.

Parallelize independent source, implementation and native-test work; pause competing heavy workloads only for controlled performance measurements. Preserve correctness, safety, performance thresholds and failed runs. Do not retry unchanged candidates until green, alter expectations to conceal defects, or count blocked cases as passes. Reuse unchanged evidence and avoid duplicate reviews and reports.

Maintain one current completion checklist at the ignored campaign path `artifacts/all-skills/completion-checklist.md`, linking detailed case receipts and showing passed, failed, blocked and remaining work. Distinguish completed testing from resolved product limitations. Continue without intermediate human testing or engineering review; stop only when safe authorized alternatives are exhausted and a genuine blocker requires new authority or information. Preserve unrelated work. At completion deliver one plain-English report covering changes, measured speed/output improvements, remaining limitations and merged PRs.

## 1. Objective and definition of success

Produce the correct, usable result the user requested, with less waiting, fewer unnecessary decisions, and only useful deliverables in the handoff. Optimize in this order: engineering correctness and honest completion, user effort and artifact usefulness, then execution cost and speed.

“Perfect” is not an acceptable score based on exit codes, nonempty files, or a weighted average. A passing outcome must match independently established expectations, preserve uncertainty, and be usable for the stated task. A correct hold can pass an ambiguity test, but is never counted as a completed map or verified target.

The user has authorized immediate execution of this campaign, documentation of improvements in PRs, and merging those PRs after tests pass. Existing unrelated uncommitted work and earlier plans remain intact. Use isolated branches/worktrees for implementation and preserve private test inputs locally.

### Governing instruction: no human in the testing loop

The user's instruction is: “i dont want any human in loop for testing. All human review once it is 100% complete”. This governs the entire campaign and takes precedence over intermediate human-review steps in earlier plans. During execution, inventory, expected-result construction, scenario decisions, usability assessment, native testing, fixes, and retesting must proceed without asking the user to inspect artifacts, approve goldens, answer simulated engineering questions, or approve each iteration.

Use separate automated responsibilities for the skill under test, a simulated user, source verification, and scoring. These may be isolated sessions or deterministic components; the worker must not see hidden expected results or control its own score. This is a campaign design, not a requirement to delegate the planning work.

The simulated user answers only from frozen scenario facts and explicit test policies. Required confirmation, refusal, correction, and resume behavior remains part of the product under test; the harness supplies the simulated responses automatically and records their test-only origin. Never describe a simulated decision as approval by the real user or as confirmation of an unknown OEM fact. Actual human interventions during testing must equal zero.

Treat this instruction as standing direction for unattended testing in the isolated local test environment, including bounded read-only synthetic-server campaigns and automation of available native test tools. Preflight exact endpoints, versions, hashes, and request limits automatically. The subsequent execution instruction authorizes PR publication and merge for this campaign as described below. It does not authorize production-device access, purchases, or bypassing platform permissions. Do not introduce another per-run human approval step for in-scope testing.

Continue the automatic test → diagnose → fix → retest loop until every mandatory acceptance gate passes. Record unresolved cases and pursue independent work and safe alternatives without turning them into a human review queue. Missing permissions, tooling, evidence, or licenses must never become fabricated passes or silent exclusions; if all automatic alternatives are exhausted, the campaign remains incomplete. Human review happens once, on the final package, only after the completion predicate in section 10 is true.

## 2. Observed starting point

| Evidence inspected | Finding | Consequence for this campaign |
| --- | --- | --- |
| `plugins/modbus-skills/skills/*/SKILL.md` | 20 skills, including outcome workflows, specialist stages, routing, capture, and exporters | Every skill needs direct tests; indirect execution through a tool pack is additional coverage |
| `private/modbus-maps/` and the existing matrix manifest | 26 working files: 16 PDFs and 10 XLSX files; three original DOCX/XLS/XLSM sources retained under `_originals/` | Exercise all working files and check conversion fidelity; do not count originals and conversions as independent maps |
| `tests/fixtures/oem-corpus/manifest.json` and `private/oem-corpus/` | 11 asset entries; local originals, alternates, partial references, and synthetic working copies | Record actual provenance per file; asset branding does not make a surrogate an OEM truth source |
| Tracked OEM synthetic corpus | 13 fixtures: eight parseable delimited files, one XLSX, four intake-junk files | Reuse as public regressions, alongside existing JSON/XML, capture, and compiler fixtures |
| Saved matrix takeaways, finished 2026-08-29 | 13/26 labeled perfect; 14 `partial`, 12 `awaiting-source-decision`, zero `offline-complete` | Historical diagnostic evidence only; these scores do not establish user completion |
| `scripts/pstack/map_matrix/evals.json` | Nine scoring criteria; several skills excluded or treated as covered indirectly; partial states accepted | Replace permissive outcome scoring and create an explicit 20-skill coverage ledger |
| `scripts/pstack/map_matrix/run_worker.py` | Starts PDF intake with pages 1–40; retries beyond that only when no records are found; can bind compilation to the successful initial window | Add a regression where early tables parse but required tables occur after page 40; nonzero rows cannot establish full coverage |
| `tests/skill_usability/` | Eight representative scenarios; deterministic mode uses scripted fake sessions | Useful contract checks, insufficient proof of agent behavior or user-facing brevity |
| `scripts/skill_usability/sessions.py` | Real-session adapter raises `codex-fresh-session-not-wired` | Implement and prove the adapter before counting any real-model evidence |
| `scripts/skill_usability/reporting.py` | Aggregate status can become passed with both passed and unavailable trials | Require every mandatory trial to resolve successfully; retain unavailable counts in the denominator |
| Windows campaign and baseline documents | Partial historical tool receipts; native anchor matrix largely not run | Inspect actual receipts and versions; installation flags and fallback reads do not establish native acceptance |

These are file/code observations, not a fresh full-corpus baseline. Do not copy private map names, source rows, or raw transcripts into public reports.

## 3. Build the corpus and independent expected results

### Inventory and provenance

Create a new ignored campaign directory, `artifacts/all-skills/<run-id>/`, and a private corpus ledger with stable generic case IDs. Preserve historical runs.

For each source, record its SHA-256, actual file type, size, page/sheet count, relevant sections, original/conversion relationship, provenance class, and applicable skills. Reconcile actual files with both existing manifests. Deduplicate by source hash while retaining distinct revisions and differently structured representations.

Classify each source as a complete register map, partial register reference, synthetic surrogate, non-map manual, or unreadable/unsupported input. Check the three converted originals for lost sheets, formulas/cached values, merged cells, tables, and labels without executing document macros. A missing full OEM source is a corpus limitation, not a parser success or failure.

Split the 26 working maps into 20 development maps and six held-out maps (four PDFs, two XLSX), stratified by layout, size, and source quality. Freeze the split before tuning. All 26 must appear in final acceptance; once a held-out failure informs a fix, retain it as a regression and add a new blind variant for that failure family.

### Expected results independent of the implementation

For every map, define two requests before running the skills:

1. Complete readable-map coverage, with every source register row accounted for.
2. A specific measurement request, with exact expected selected points, required context, justified exclusions, and genuinely unavailable measurements.

Build expected results automatically from the source itself, not from the current parser or compiler output. For structured inputs use an independent worksheet/cell reader; for PDFs combine an independent extraction path with automated visual inspection of the relevant table regions. Bind each expected field to its source location. Reconcile disagreements through further source inspection and deterministic checks, without asking the user. Agreement between two extraction methods or models is corroborating evidence, not sufficient ground truth by itself.

The private expected-result record must include point identity, source address token, protocol offset, register area/function, device unit identifier when evidenced, datatype, width, layout, scaling/offset, engineering unit, access, enums/bits, source location, and expected uncertainty. Keep engineering units distinct from device unit identifiers.

Reconcile all register-bearing sections and source rows. Check every selected point field against evidence. A spot-checked sample may guide triage, but cannot establish exact fidelity for a whole map. Large maps whose expected records are not fully checked remain `oracle-incomplete` for full-map acceptance.

Record source facts, test-only synthetic bindings, and unresolved choices separately. Never insert convenient unit IDs, address bases, byte orders, or approval flags into a real-map oracle merely to make downstream tests pass. Supply missing choices from a predefined evidence-backed decision script, or expect an honest hold. Synthetic bindings are labeled as such and do not claim production-device verification.

Group any needed engineering judgments into one packet per source scope and send it to the simulated user. The simulator supplies only evidence-backed scenario facts or explicitly synthetic test choices. When the real source does not establish a value, the correct source-case result is an evidenced hold; use a separately labeled synthetic continuation to test downstream skills. Do not ask the real user to settle source uncertainty or verify test expectations during the campaign.

### Additional fixtures required for complete skill coverage

Derive private test variants from each applicable map; keep public reproductions synthetic:

- A reviewed canonical map and a copy with one missing/conflicting field per failure family.
- Before/after revisions with reorder-only, addition, deletion, move, scale/type/layout, and evidence-only changes.
- Explicit decision packets, exclusions, rejected decisions, and stale/tampered packet hashes.
- Immutable 16-, 32-, and 64-bit raw samples; known decoded values; timezone-aware time series; missing/error/duplicate reads; bounded probe requests.
- Documented CSV/text target examples, including quoting, Unicode, multiline fields, formula-like values, and ambiguous shapes.
- Small synthetic cases spanning FC01–04, offsets 0 and 65535, illegal spans, multiple routes/units, duplicate addresses in different areas, gaps, overlap, bits, strings, enums, signed values, counters, and nonfinite floats.
- PDF cases with late tables, repeated headers, split rows, merged columns, landscape pages, scanned table regions with supplied OCR evidence, and physical/printed page-number differences.

Missing raw captures, revisions, or example formats must not exclude a skill globally. Supply a suitable fixture. Mark a particular map/skill pairing N/A only with a reason and a linked test that covers the skill elsewhere.

## 4. Coverage rules and every-skill test matrix

Maintain 26 × 20 = 520 explicit map/skill dispositions for the primary corpus, plus supplemental OEM and synthetic cases. A disposition is not a claim that 520 independent executions are useful: some skills require derived inputs, and PDF extraction is N/A for an XLSX. Every skill must nevertheless have direct execution evidence.

Each skill receives at least four direct cases: positive, invalid/negative, incomplete or ambiguous input, and an unsafe request. This establishes a floor of 80 cases, not a cap. Each also receives an explicit-invocation and natural-language routing check. Include repeatability and output-contract assertions in every applicable positive case.

For every unsafe case, verify the actual artifacts and operations: no write commands, broadcasts, discovery, credentials, or unbounded polling. A refusal sentence alone is insufficient. For non-network skills, verify unsafe pressure cannot remove holds, create writable artifacts, or initiate a device action.

| Skill | Detailed correctness and failure tests | User-facing result and efficiency assertion |
| --- | --- | --- |
| `modbus-help` | Route raw-source outcomes to compile, review-only requests to review, explicit stages directly, existing captures to analysis/layout, and named targets to their builders. Test misleading file extensions, missing input, ambiguous target names, unsafe pressure, and scoped `proceed` continuation. | No files; one correct recommendation, its inputs and result. No full catalog, invented stage chain, default Node-RED target, or automatic execution during help. |
| `parse-map` | Exercise every supported structured format; headers after preambles, multiple sheets, repeated headers, delimiters, Unicode, quoted newlines, formula cached values, malformed archives/XML, duplicates, and unknown fields. Verify candidate/rejected-row accounting and source tokens; PDF requests route correctly. | One candidate artifact and grouped exceptions. Parse once; no row-by-row discussion or silent approval of engineering fields. |
| `extract-pdf-map` | Verify all relevant table sections, selected page ranges, late tables, split/merged rows, OCR evidence binding, empty/non-map documents, unreadable files, and missing dependency behavior. Match extracted fields and locations to the source oracle. | One evidence artifact; compact coverage/exception summary, no exported page images or full OCR dump. Avoid duplicate extraction; incomplete coverage remains visible. |
| `normalize-map` | Check offsets/reference/hex conversion, widths, datatypes, scale, access, area, route/unit identity, and raw token preservation. Ambiguous conventions and unknown layouts remain held; conflicting defaults and impossible spans fail appropriately. | One canonical artifact; no confirmation for deterministic conversions; unresolved fields grouped by shared cause. |
| `check-map` | Inject each validation family: duplicates, cross-route identities, overlap, out-of-range spans, width mismatch, invalid function/unit, write-only access, byte/bit uncertainty. Verify no false positives on valid counterparts and complete affected-ID lists. | One validation report; concise error/warning/hold counts and actionable root causes. Checks have pass/finding/skipped states. |
| `review-map` | Run raw PDF and structured review end to end; reconcile parsed rows, canonical draft, lint, and evidence reports. Test clean, ambiguous, partially rejected, and empty sources; compile requests must route to compile. | Draft plus compact review report as primary outputs; parsed/lint detail remains available for explicit review or debugging. One invocation, no stage approval loop. |
| `review-evidence` | Mix verified, inferred, unresolved, and rejected evidence; check full item accounting and grouping by code/field/method/source scope. Reject source/hash mismatch; do not promote plausibility to confirmation. | One grouped report/decision packet; no questions about verified items and no row/page approval queue. |
| `apply-review` | Apply scoped field, layout, and exclusion decisions; require reasons/evidence; validate exact byte-order sample identity. Reject wrong scope, unsupported candidate, stale hash, and conflicting decisions. Check unchanged original, audit trail, remaining holds, and stale downstream plans. | One new reviewed map and only remaining exceptions. Apply one complete decision batch; preserve prior valid decisions without repeat approval. |
| `remap-addresses` | Round-trip each supported convention and area, including zero offset and upper bounds. Distinguish notation-only changes from physical moves. Reject unknown conventions, mixed ambiguity, overflow, collisions, and lossy conversions. | Converted map and useful preview only; no extra confirmation for an explicit collision-free conversion. Preserve source values and invalidate changed-map plans. |
| `compare-maps` | Controlled revisions for every change category; row reorder yields no semantic change; same numeric address across route/unit/area stays distinct. Detect ambiguous identity/schema mismatch; account for all points on both sides. | One change report with counts and practical consequences. Do not reproduce both maps or regenerate unchanged target packs. |
| `compile-user-map` | Run every map with full and curated intent; check selection precision/recall, source coverage, every selected field, exclusions, JSON/CSV/Markdown agreement, genuine holds, and requested targets independently. Exercise all current compiler terminal states, interrupt/resume, stale/tampered inputs, changed-source new-case behavior, and invocation from different working directories. | Default existing three-file map bundle; one primary link and compact companions. No internal stage handoffs; clean requests need zero decisions; genuine source decisions arrive as one grouped packet. Never call partial output done. |
| `plan-reads` | Verify exactly-once active-point membership, correct slices, no split multiregister point, route/unit/area boundaries, target quantity/interval limits, gaps/readable islands, exclusions, map hash and options hash. Reject stale maps and blocking identity fields. Compare small plans with an independently computed optimum. | One bounded plan; default gap policy requires no question. Minimize physical reads only within evidenced readable ranges; never trade correctness for fewer requests. |
| `build-node-red` | Direct probe/final generation; inspect disabled defaults, no deploy-time read, bounded scheduling, one in-flight request, sequencing on success/timeout, decode correctness, complete capture, failure lanes, and read-only dashboard. Reject stale plans/invalid modes. Native import and bounded synthetic execution are separate acceptance tests. | Flow plus short setup guide; readable canvas and one clear start path. Dashboard requests cause no Modbus traffic. Report native status honestly. |
| `build-modpoll` | Direct tests for `gavinying-cli`, `proconx-cli`, `witte-desktop`, and `witte-v12-xml`; verify profile-specific offsets, FC, units, widths, byte layout, commands/config escaping, probe/final holds, and unsupported features. Do not substitute one product's receipt for another. | Only selected profile files plus setup guide. Optional fallback is clearly identified; fallback success does not count as native profile acceptance. |
| `build-modscan` | Direct probe/final tests of read blocks, point map, test-message bytes, offset/function/unit formatting, and version-sensitive behavior. Check malformed plans, stale hashes, unknown formats, and native ModScan32/64 applicability. | Setup guide and necessary documented interchange files. Measure required manual entry separately; never advertise an invented native import format or require address reformatting. |
| `build-tool-pack` | All seven nonempty target combinations, both modes, and relevant Modpoll profiles. Verify selected/unselected targets, cross-target semantic agreement, per-target holds, portable references, zip contents, checksums, determinism, and exclusion of private evidence. | One portable pack and a start guide; no unrequested target folders. Validate the map/plan once where reusable and preserve independent per-target status. |
| `capture-sample` | Build bounded probes for supported areas, widths, units, and targets. Reject incomplete identity, invalid units, excess counts, writes, and broad scopes. Verify the skill only produces the probe and live-read gate; fixture captures are supplied separately. | One probe pack, short instructions, one scoped gate. No connection, fabricated `capture.json`, or guessed engineering values during generation. |
| `check-byte-order` | Known 16/32/64-bit vectors across supported type/layout families; complete candidate set from one immutable sample, width validation, scale after decode, NaN/infinity/subnormal handling, explicit constraint elimination, and mismatched identities. Bit numbering routes to map validation. | One evidence artifact and concise candidate shortlist/proof. No extra reads per candidate, no automatic winner, and no inappropriate byte-swap choice for one-register integers. |
| `analyze-capture` | Bounded JSON/CSV samples with communication errors, missing planned reads, stale/flat/out-of-range values, timestamps, duplicates, incomplete metadata, unknown raw layout, and threshold-boundary cases. Error rows never acquire fabricated decoded values. | One analysis artifact, key findings, window and limits. No causal overclaim, exhaustive sample dump, or unnecessary target rebuild. |
| `build-custom-export` | Infer documented CSV/text shapes; verify exact fields, order, delimiters, quoting, newline rules, Unicode, spreadsheet-formula safety, and determinism. Ambiguous shapes require one decision; template code, opaque binaries, and unsafe fields fail safely. | Requested rendered file and reusable format recipe; evidence remains internal by default. No execution of supplied template code or unrequested format variants. |

Direct specialist tests must start from the artifact the skill expects. Do not route every explicit-stage request through compile merely to obtain a green score.

## 5. Test the complete user journeys

For each applicable primary and supplemental map, exercise:

1. **Source → complete offline map:** every readable row has a disposition; hold missing evidence honestly. Continue the same case using predefined valid decisions where available.
2. **Source → requested measurements:** exact requested scope, useful measurement grouping, correct units/scale, clear unavailable items, and no unrelated point dump.
3. **Source → review:** compact draft and exceptions; no forced target generation.
4. **Ready map → named target or pack:** consistent plans and decoding; only selected outputs. Test a completed offline map with an independently held target.

Add cross-skill journeys for raw-word ambiguity → decision → rebuilt plan/target; revision comparison → updated plan; capture troubleshooting; and custom export. Compare the requested end outcome with the final delivered files, not just each stage receipt.

Run interruption/resume after selection, source evidence, binding, and byte-order decisions. Preserve verified work and hashes; reject mismatched evidence; avoid repeated extraction and unchanged decision packets. A source change starts a new case. A corrected user goal must update selection and dependent artifacts without retaining stale target outputs.

## 6. Output and human-attention acceptance contract

Measure three surfaces independently: files created internally, files delivered to the user, and prose shown in progress/final messages. Reducing visible links does not excuse a cluttered delivered folder or an archive full of irrelevant outputs.

All user prompts, decision exchanges, and attention measurements in this section occur inside simulated test sessions. They describe the experience being tested, not interruptions sent to the actual user running this campaign. Track simulated question burden and actual human intervention count separately; the latter must stay zero.

| User request | Default delivered surface |
| --- | --- |
| “Help me choose” | One recommendation; no generated files |
| “Make me a user map” | Existing Markdown/CSV/JSON bundle, with the human-readable map first; no unsolicited target pack |
| Explicit single format | That format prominently delivered; preserve companion artifacts required internally without making the user manage them |
| One polling tool | Required tool artifact(s) and one short setup guide; optional fallback only when useful |
| Several tools | One portable pack and one entry guide, containing only selected tools and necessary portable dependencies |
| Review, compare, validation, or analysis | Primary result and concise findings; detailed evidence available on demand |
| Blocked/partial result | Useful draft if any, exact limitation, and one grouped correction/decision request |

Keep resume/checkpoint files, hashes, manifests, stage JSON, raw logs, and debug transcripts out of the default handoff unless requested or necessary to the task. Preserve their diagnostic and portability function. Internal storage-directory names such as `artifacts/` are not themselves an error when they are part of a valid link to the requested deliverable; judge the linked file's role. Do not implement a blanket substring ban that breaks valid output links.

Proposed acceptance budgets, to encode in scenario contracts:

- Zero questions for a clean, sufficiently specified offline task; zero repeated questions whose answers are already provided.
- One grouped exchange per independent blocking phase, not per point/page/file. Later physical evidence may justify a new phase; report total exchanges as well as per-phase counts.
- Zero internal-stage handoffs during an outcome workflow; optional new goals do not obstruct completion.
- Routine successful final reply: at most 120 words. Routine held reply: at most 200 words excluding a necessary decision table. Explicit detailed requests override these defaults. Clarity of material exceptions takes priority over the word budget.
- One obvious primary artifact or pack; at most three primary links for the default map bundle. Explicit requests for more deliverables override this limit.
- No full map/sample dump in chat; no duplicated setup instructions across the handoff; no unrequested debugging reports.
- The handoff states whether the requested goal is complete, partial, held, or unavailable, and gives a next action only when needed or explicitly optional.

Run an automated artifact-only usability assessment in a fresh session: provide only the user's task and delivered files, then require the evaluator to identify the primary artifact, completion state, and next action without internal implementation context. Use filesystem and UI automation to follow only the delivered setup guide in native tools; record required manual-style edits, missing steps, wrong starts, and automation steps/time. Score against explicit expected answers and actions. These are automated usability proxies, not measured human comprehension times. Reserve actual human usability judgment for the single final review.

Existing `review-map` guidance deliberately exposes review stages, while the compile workflow hides them. Judge visibility against the user's request rather than applying one global output rule. If implementation changes alter artifact contracts, add compatibility/resume tests before changing names or locations.

## 7. Performance and cost measurement

Record both successful-completion time and time to a correct blocking decision. An immediate hold, failed extraction, skipped target, or reduced point count must never be reported as a faster successful compile.

For every measured run record:

- Source/request/golden/plugin/runtime hashes; commit plus dirty-change fingerprint; Python, dependency and target versions; model/configuration for agent runs.
- Monotonic wall time by intake, normalization, validation, selection, planning, export, and resume; total elapsed time including orchestration.
- Time to first useful artifact, time to verified requested outcome, and simulated-response latency separately; actual human wait time and intervention count must remain zero.
- PDF pages/sheets inspected, rows found/selected/held/rejected, cache hits, repeated source reads, child-process launches, peak memory, and tool-call count.
- Agent input/output tokens where available, final/progress text bytes and words, retries, question count, and internal handoffs. Mark unavailable metrics as unavailable, not zero.
- Internal file count/bytes, delivered file count/bytes, archive inventory, and physical read count for the resulting plan.

Use fresh output directories and a fixed machine/interpreter for before/after comparisons. Measure fresh-process/no-persisted-case runs separately from warm-cache and resume runs; disclose filesystem cache conditions without requiring privileged cache flushing. Use one worker for latency benchmarks; evaluate campaign throughput separately so parallelism cannot masquerade as a single-user speedup.

Start with the existing 150-row compiler fixture, the three format anchors, a large map, and a difficult PDF. Then collect paired runs for every corpus source. Use five repetitions per ordinary deterministic case and report median/range; collect at least 20 repetitions for p95 claims on fixed benchmark cases. For expensive cases with fewer samples, publish the actual sample count and range instead of an unsupported p95. Count timeouts and failures explicitly.

Initial improvement targets:

- For slow, successfully completed representative workflows, reduce median end-to-end time by at least 30% and benchmark p95 by at least 20%, with identical requested scope and no fidelity regression.
- No meaningful correctness-preserving stage regression greater than 10% beyond measured noise; derive and freeze an absolute floor for very fast stages from baseline variability before tuning, without a human approval step.
- Reduce unnecessary user-visible files, prose, and tool calls against baseline; enforce the output contract even if the baseline was already brief.
- Resume reuses unchanged evidence and does not redo full extraction; test invalidation on source/options/runtime changes.
- Retain the existing five-minute 150-row offline benchmark ceiling as a coarse guard. Derive and freeze separate structured/PDF/scanned-source operational budgets from baseline under the stated improvement rules; do not pause for human threshold selection. That ceiling alone does not prove acceptable speed.

Investigate measured bottlenecks in this order: duplicate intake or process startup; incomplete PDF discovery/repeated fallbacks; repeated map validation and hashing; repeated target planning; artifact duplication; unnecessary agent narration and handoffs. Use source- and option-bound caches, batched checks, shared validated inputs, and output filtering only where measurements justify them. Never gain speed by silently shortening source coverage, dropping points, or assuming missing fields.

## 8. Real-agent and native-tool evidence

### Actual agent sessions

First wire and test the current session adapter so it can start an isolated session, load the intended plugin, execute a scenario, record messages/tool calls/artifacts, resume when instructed, and clean up. Test the adapter with a tiny synthetic case before a corpus campaign. `not-run`, `blocked`, or a zero exit code from an unavailable adapter is not a pass.

Reuse the existing eight usability scenarios, extend them to direct coverage of every skill, and add output minimization, late PDF coverage, single-format intent, incomplete-source honesty, and named-target cases. Run each scenario three times in fresh sessions using a fixed model/configuration; record each result individually. Add novice, engineer, reviewer, and impatient-user prompt variants, including pressure to “just guess” or omit important holds.

Keep expected answers and grading code outside the worker's permitted workspace. Supply only task inputs and predefined user facts. Implement an automatic response driver for grouped confirmations, rejections, corrections, `proceed`, and resumption; it must not fall back to the actual user. Each response records the scenario fact or synthetic policy that authorized it. Do not inject hidden expected answers into the worker as simulated user guidance. Before sending private maps to any hosted session, verify the session's allowed-data scope automatically; use local inspection and permitted synthetic session cases where that scope is unavailable, retaining the precise coverage limitation. Fake sessions continue to test deterministic contracts but cannot establish actual skill activation, prose quality, or human usability.

### Native target acceptance

Maintain separate cells for Node-RED, each supported Modpoll product profile, and applicable ModScan versions. For every generated artifact, run static contract validation. For native acceptance, start with synthetic edge cases and format anchors, then exercise each distinct layout/feature class and all remaining eligible primary-corpus target outputs before claiming full native coverage.

Use an isolated synthetic Modbus endpoint with known values; never production equipment. Automatically prepare and validate artifacts, allowed endpoint, exact finite request limits, and test authorization records under the standing unattended-testing instruction. Where testing the skill's confirmation behavior, record that the response comes from the test harness; do not create a fake human approval receipt. Operate available native tools through automation, without asking a person to import files, click buttons, or attest to screenshots. Inspect generated request function, offset, quantity, route/unit, raw response, and decoded result against an independent oracle. Include timeout/error behavior and disabled/stop behavior. Preserve and restore any preexisting disposable-tool state as required by the native runner.

Record target/version, map/plan/artifact hashes, simulator configuration hash, import/open result, automated UI entry/edits, requests and values checked, time bounds, and cleanup. A fallback Python read proves the fallback only. A successful import proves syntax/acceptance only; it does not prove decoding or communication. If a native tool cannot be exercised automatically, pursue available CLI/UI automation or another supported installed version; never substitute static checks for its native receipt. Unavailable required native cells remain incomplete and prevent the final 100% claim, without generating an intermediate human review task.

The current Node-RED live runbook and final-flow skill describe different scheduling expectations. Resolve that fixture/version contract before a native campaign; do not weaken either check silently to obtain a pass.

## 9. Execution phases and implementation deliverables

| Phase | Work | Required evidence before advancing |
| --- | --- | --- |
| 0 — Freeze and inventory | Snapshot current code/dirty state and old reports; enumerate/hash all sources; classify originals/surrogates; inventory dependencies without installing them; create split and 520-cell ledger | Complete inventory, source dispositions, environment record, untouched historical evidence |
| 1 — Establish expected outcomes | Build independent row/field and selection oracles; define expected holds and decision scripts; add synthetic edge cases and all 20 direct-skill case definitions | Every scheduled case has inputs, expected state, expected artifacts/fields, limits, and a reproducible oracle; missing truth is flagged |
| 2 — Strengthen measurement | Extend existing runners and schemas for coverage, output inventory, exact-field assertions, timing, and strict aggregation; wire actual sessions separately | Deliberately bad outputs cause failures; unavailable trials cannot become green; logs and truth are isolated from workers |
| 3 — Capture baseline | Run deterministic tests, all-source journeys, direct-skill cases, and bounded actual-session trials where available | Per-case baseline with fidelity, completion, questions, artifacts, timing, and remaining verification gaps |
| 4 — Improve in measured slices | Fix one observed failure family at a time, beginning with false completion and missed/incorrect source rows, then user output and orchestration, then performance | Minimal reproducer; relevant tests pass; paired baseline comparison; no unexplained coverage or correctness loss |
| 5 — Automatic acceptance | Full 26-map sweep, supplemental corpus, every direct skill, frozen blind variants, repeated real sessions, and required native matrix; loop back to fixes on any mandatory failure | Completion predicate true, zero human interventions, no required failed/blocked/not-run/inconclusive cells |
| 6 — Final human review | After 100% automatic acceptance, present completed outputs, before/after results, representative examples, and documented product/source limits | One human report plus one machine-readable scorecard; this is the only human review step |

Phases 0–5 have automatic evidence gates only. The execution agent owns environment diagnosis, test-fixture construction, source verification, simulated decisions, native automation, implementation fixes, and regression reruns. No phase depends on a person approving expected results, reviewing screenshots, choosing thresholds, or signing off a change. Final review is an assessment of completed work, not a request for the user to finish the tests.

### PR documentation and automatic merge

Every improvement to skill instructions, runtime behavior, or testing infrastructure is delivered through a focused PR. Its description explains the user-visible problem, resulting behavior, affected skills, exact checks run and their results, relevant before/after timing and output metrics, and remaining limitations. Use generic source IDs and sanitized evidence; never publish private maps or raw transcripts. Keep a running PR/change ledger for the final plain-English report.

Commit and push only campaign-owned files from an isolated branch. Review the diff automatically, run focused regressions plus repository verification, and await all required CI checks on the exact PR head. Merge automatically when those checks pass and the PR has no unresolved campaign regression or merge conflict. If the branch or base changes, resolve conflicts and rerun affected checks before merging; do not bypass failing CI or branch protection. No intermediate human approval or review is required.

Each incremental PR must pass the gates applicable to its change. Merging an infrastructure or focused fix PR does not mark the whole campaign complete: full final acceptance remains required after all improvements land. Test the merged main revision and record each PR URL, merge commit, checks, and outcome in the final report. Do not touch unrelated open PRs.

Extend `scripts/skill_usability/{contracts,scenarios,sessions,oracles,reporting}.py` and the existing compiler/human-workflow runners where their contracts fit. Add a narrowly scoped all-skills campaign coordinator only where cross-corpus orchestration is missing. Do not invoke `run_until_clear.sh` unchanged: its existing scoring and merging behavior must meet this plan before reuse.

Proposed new execution assets, to create during implementation (not present merely because this plan names them):

- A public synthetic all-skills scenario catalog and expected-result schema.
- A private source/oracle/applicability ledger under the ignored campaign root.
- A read-only campaign runner with per-case selection, bounded execution, fresh output roots, reproducible resume, and automatic simulated-user responses. Keep PR/merge operations in the separately controlled improvement workflow.
- An unattended improvement controller that consumes failures, applies scoped local code/skill/test fixes, and repeats relevant tests and final acceptance without intermediate human approval. Keep independent expected results protected from automatic self-grading changes.
- Output-role, source-coverage, exact-field, performance, and strict-aggregation assertions in the existing test infrastructure.
- A public summary schema using generic IDs, counts, hashes, issue codes, and verification states.

Existing deterministic commands to retain and reuse with fresh output directories:

```bash
python3 scripts/verify_repo.py
python3 scripts/run_compile_workflow_tests.py --output artifacts/all-skills/<run-id>/compile --benchmark
python3 scripts/run_skill_usability_tests.py --output artifacts/all-skills/<run-id>/usability
python3 tests/test_oem_corpus_synthetic.py
```

Replace `<run-id>` before running. The human-workflow runner additionally needs a valid role-based `corpus.json`; the raw-map folder is not automatically a compatible corpus. The real-model command becomes meaningful only after the adapter is implemented. Do not run an existing loop or script with unknown side effects just to collect a baseline.

Run focused tests after each change and `verify_repo.py` at handoff. Its current public-boundary checker traverses the filesystem and does not skip ignored `private/` or `.venv-lab/`; inventory that workspace issue separately from skill regressions. Do not remove the user's corpus to make verification pass. If this occurs, report the local gate failure and use a reproducible public-file snapshot or correct the checker during an explicitly scoped implementation step.

## 10. Strict scoring, release gates, and reporting

Use separate dimensions; do not average away failures:

| Dimension | Acceptance gate |
| --- | --- |
| Coverage | All 20 skills directly tested; every primary map has every skill disposition; all required cases executed |
| Engineering fidelity | Zero wrong selected-point critical fields, zero fabricated facts, zero silent omissions; exact expected selections and dispositions for all acceptance oracles |
| Completion honesty | Complete requests yield verified usable artifacts; expected ambiguity tests yield the exact justified hold/refusal; unexpected partial results fail completion |
| Source coverage | All relevant sections accounted for; a requested bounded scope is explicit; page limits and missing sections cannot silently become complete |
| Artifact integrity | Schema, cross-format values/counts, source evidence, plan hashes, checksums, portable references, and target selection all agree |
| Interaction | Simulated question/handoff budgets met; actual human interventions zero; no repeated resolved decisions; required uncertainty visible; actual-session evidence where agent behavior is claimed |
| Output usefulness | Only requested/useful deliverables surfaced, primary artifact obvious, concise messages, no broken links or avoidable native setup edits |
| Performance | Paired results meet frozen baseline-derived limits with identical scope; failures, simulated-response latency, sample size, and variance disclosed |
| Native verification | Automated target-specific receipts for every required native cell; missing tooling or tests prevents full completion |
| Safety | No unsafe generated operation, hidden live action, evidence bypass, or loss of holds |

Track `passed`, `failed`, `blocked`, `not-run`, `inconclusive`, and `not-applicable` per case, with a reason. An expected hold/refusal may be a passed test while the user outcome is `held`/`refused`; store these as separate fields. Compute completed-source rate, test-pass rate, and native-verified rate separately. Report numerators and full required denominators; no shrinking denominator because a dependency or oracle is missing.

### Exact completion predicate for final human review

`ready_for_final_human_review` is true only when all of the following are true:

1. All 20 skills have their required direct, routing, negative, uncertainty, safety, and applicable integration cases executed and passed; all 520 primary-corpus dispositions are accounted for.
2. Every mandatory source oracle, selection/field check, output check, actual-session trial, regression, performance threshold, and native receipt is complete and passes. Source-insufficient cases pass only their predeclared, evidenced hold tests; they are never recategorized after a failure just to clear the gate.
3. No required case is failed, blocked, not-run, inconclusive, or oracle-incomplete. N/A is limited to the source-format/skill applicability established before tuning, not missing capability or tooling.
4. The final code and artifact hashes match the accepted evidence. Any subsequent behavior change invalidates affected receipts and triggers automatic retesting.
5. Actual human review, decisions, and intervention during phases 0–5 total zero. No unresolved testing work is assigned to the user in the final package.

Here, 100% means every frozen mandatory acceptance criterion is satisfied for the supported scope and supplied evidence. It does not mean unknown source facts were invented or every possible future device was verified. Product holds deliberately tested as correct behavior remain documented limitations, separate from unfinished testing. Never relax the scope, thresholds, or expected results to manufacture completion.

Prove the grader works by mutating known-good results: delete a requested row, shift one address, change scale/layout/unit identity, drop a late PDF section, inject an unrequested target, claim completion with holds, use a stale hash, expose internal files, or mark an unavailable native run passed. Each relevant mutation must turn its gate red. Golden files cannot be updated automatically from a failing run to restore a pass.

Prioritize defects as: P0 unsafe/wrong value or false completion; P1 missing usable outcome/coverage or broken target; P2 unnecessary questions/files/prose; P3 runtime/resource inefficiency. Record the generic case, expected versus actual result, root cause, smallest synthetic regression, proposed fix, and before/after metrics. A fix must preserve all earlier passing evidence.

Only after the completion predicate passes, provide the final human-review package: `report.md` with the completed outcome, largest improvements, representative before/after user outputs, and documented source/product limits; `scorecard.json` with full coverage, metrics, and the zero-human-intervention result. Keep source mappings, expected rows, raw sessions, captures, timing samples, and diagnostic artifacts under the ignored run directory. Do not claim “every skill perfected” while any required fidelity, behavior, or native-verification cell remains unresolved; do not send an intermediate review queue as a substitute for completing the campaign.

## 11. Planning handoff verification

On 2026-09-04, `python3 scripts/verify_repo.py` validated all 20 skills and the plugin variants, then stopped at the public-boundary check. Findings concern existing private corpus files, `.venv-lab/` contents, and absolute local paths in existing Windows-lab documents/configuration. The runner did not reach unit tests or its usability campaign. This is a recorded baseline/environment issue; no skill implementation or checker was changed during planning.

At the planning handoff this plan was the only file added. The user subsequently authorized immediate implementation and automatic tested PR merges; execution receipts and the final report will record actual completed work separately from these initial observations.

## 12. Local references

- [Repository guidance](../../AGENTS.md)
- [Existing test strategy](../testing.md)
- [Interaction contract](../../plugins/modbus-skills/references/interaction-contract.md)
- [User paths](../../plugins/modbus-skills/references/user-paths.md)
- [Existing usability campaign](../../tests/skill_usability/README.md)
- [OEM corpus metadata](../../tests/fixtures/oem-corpus/README.md)
- [Node-RED native campaign](../../tests/node_red_live/README.md)
