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
