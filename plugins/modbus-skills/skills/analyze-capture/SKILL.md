---
name: analyze-capture
description: Analyze bounded Modbus samples and report communication, timing, signal, and raw-word evidence. Use when the user has capture data, JSON/CSV samples, or asks about stale, missing, flatline, or signal-quality problems.
license: Apache-2.0
---

# Analyze Capture

Turn supplied samples into bounded evidence.

Follow `../../references/interaction-contract.md`.

## Process

1. Require a bounded `capture/v1`, CSV, or JSON input.
2. Read `references/analysis-options.md` before choosing thresholds.
3. Run `python3 <skill-dir>/scripts/run.py --input <capture> --options <options.json> --output <analysis.json>`.
4. Separate communication findings from signal findings.
5. When the capture names expected request IDs, report missing planned reads as a campaign error.
6. Report the sample window, thresholds, skipped checks, and missing metadata.
   Read each point's `checks` inventory. A missing threshold is a skipped test,
   not proof that the signal is healthy; `stale: null` means it was not evaluated.

## Output files

- `analysis.json` - Open this result. It summarizes communication, missing planned reads, timing, signal, and raw-word findings for the supplied sample window.

Completion requires a schema-valid analysis that makes every enabled check visible.

## Stop

- Stop for unbounded captures, writes, broadcasts, or discovery.
- Stop when sample identity is missing or a timestamp lacks a timezone.
- Treat correlation as evidence, not cause.

## Handoff

- Raw-word ambiguity: suggest `check-byte-order`.
- A map revision may explain the change: suggest `compare-maps`.

Keep the result read-only. Treat correlation as evidence, not cause.
