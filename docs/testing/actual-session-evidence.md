# Actual skill-session evidence

The usability runner now supports bounded, ephemeral Codex app-server sessions.
Use `--mode real-model --scenario 02-clean-compile --repetitions 1` for a scoped
smoke test, or omit scenario selection for the configured campaign. The model
can be pinned with `--model`; reports retain the actual model and timing.

Workers can read the copied plugin and supplied fixtures and write only their
trial workspace. Network access and approvals are disabled. Unexpected server
requests, expired deadlines, excess output, and failed turns do not pass.
Private transcripts and output snapshots are retained under the ignored report
directory; they are not public reports or additional user deliverables.

Persisted required files, JSON schemas, physical point values, and compiler
completion are checked independently of the worker's final message. Raw-source
intake generates canonical point IDs; the clean-source golden checks the source
name and engineering fields, not an ID from the separate prebuilt OEM-map path.

This adapter is initial infrastructure, not proof of the entire all-skills plan.
Explicit skill attachment is not an independent discovery test. Multi-session
recovery, adversarial action classification, native application verification,
complete source goldens, and repeated performance comparisons require their own
evidence. A selected smoke test must not be described as full campaign coverage.

Scenario v2 corrects hidden filename assumptions in normalization, byte-order,
and revision-comparison trials: those prompts never requested a filename. The
oracles now require persisted semantic artifacts and check exact normalized
point values, the complete 12-candidate layout/type set, and the exact moved
point. Compiler deliverable names remain required by the compiler contract.
Markdown in a recommendation does not change the recommended skill. Actor
facts are sent at most once; an unanswered question remains observable rather
than causing a harness exception. Real-model runs are labeled simulated-user,
not deterministic. Earlier failed receipts remain historical diagnostics and
are not retroactively counted as passes under the revised oracle.

The exact-value oracle exposed a real defect previously hidden by the scripted
worker's success event: a confirmed address-convention default did not fill an
empty spreadsheet cell. Normalization now fills absent/blank conventions while
preserving nonblank source claims, including explicit unknowns, for review.
