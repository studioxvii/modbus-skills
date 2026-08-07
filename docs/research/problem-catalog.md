# Researched Problem Catalog

Each public skill must connect a recurring Modbus problem to a reproducible fixture and a tested workflow.

Machine-readable source records live in `research/issues.json`. These records use link-only evidence from official specifications, official tool documentation, and project-maintained issue trackers. See `methodology.md` for source, rights, and clean-room rules.

| Problem | Workflow |
|---|---|
| 0-based, 1-based, and 3xxxx/4xxxx address confusion | `remap-modbus-addresses` |
| Register area omitted or mixed | `normalize-modbus-map` |
| Duplicate and overlapping points | `lint-modbus-map` |
| Unknown byte and word order | `evaluate-modbus-byte-order` |
| Too many individual polls | `compile-modbus-read-plan` |
| Recreating tool setup by hand | Target generator skills |
| One map must feed several tools | `build-modbus-tool-pack` |
| Stale, flat, missing, or implausible values | `analyze-modbus-capture` |
| Register maps change between firmware revisions | `compare-modbus-maps` |
| Manual tables lose source evidence | `parse-modbus-map` and `review-modbus-evidence` |

Before adding a new skill, add the problem, common user phrasing, source links, a rights state, a synthetic fixture, and an observable acceptance test.
