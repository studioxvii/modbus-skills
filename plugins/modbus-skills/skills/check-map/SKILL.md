---
name: check-map
description: Check a normalized Modbus map for identity, range, overlap, width, access, function, and byte-order problems. Use when the user wants deterministic lint/validation findings on a normalized or reviewed register map.
license: Apache-2.0
---

# Check Map

Run deterministic checks and return structured findings.

Follow `../../references/interaction-contract.md`.

## Process

1. Run `python3 <skill-dir>/scripts/run.py --input <map.json> --output <validation.json>`.
2. Group findings by `error`, `warning`, and `hold`.
3. Group findings by code and root cause; include representative composite identities
   plus the complete affected-ID list in the artifact.
4. Hold final generation automatically while an error or hold remains.

## Output files

- `validation.json` - Open this report. It lists passed checks and groups problems by root cause. It does not create or modify a map.

Completion requires every check to have a visible pass, finding, or skipped reason.

## Stop

- Hold final generation while an error or hold remains, including packed-bit numbering.
- Do not guess byte order, bit order, address convention, or register area.
- Permit read functions 01 through 04 only.
- Stop for writes, broadcasts, discovery, or unbounded polling.

## Handoff

- Findings need human evidence: suggest `review-evidence`.
- Packed-bit or coil numbering is unresolved: keep the hold; do not suggest `check-byte-order`.
- The map passes and is reviewed: suggest `plan-reads`.
- The source is still raw or mixed: suggest `review-map`.

Use route, unit, area, protocol offset, and logical point identifier as identity. Permit read functions 01 through 04 only.
