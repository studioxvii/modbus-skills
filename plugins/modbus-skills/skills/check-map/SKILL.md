---
name: check-map
description: Check a normalized Modbus map for identity, range, overlap, width, access, function, and byte-order problems.
---

# Check Map

Run deterministic checks and return structured findings.

## Process

1. Run `python3 <skill-dir>/scripts/run.py --input <map.json> --output <validation.json>`.
2. Group findings by `error`, `warning`, and `hold`.
3. Show composite identity and source evidence for each finding.
4. Confirm final generation is held while an error or hold remains.

Completion requires every check to have a visible pass, finding, or skipped reason.

## Handoff

- Findings need human evidence: suggest `$review-evidence`.
- The map passes and is reviewed: suggest `$plan-reads`.
- The source is still raw or mixed: suggest `$review-map`.

Use route, unit, area, protocol offset, and logical point identifier as identity. Permit read functions 01 through 04 only.
