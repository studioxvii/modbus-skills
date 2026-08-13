# Researched Problem Catalog

Each public skill must connect a recurring Modbus problem to a reproducible fixture and a tested workflow.

Machine-readable source records live in `research/issues.json`. These records use link-only evidence from official specifications, official tool documentation, and project-maintained issue trackers. See `methodology.md` for source, rights, and clean-room rules.

| Problem | Workflow |
|---|---|
| 0-based, 1-based, and 3xxxx/4xxxx address confusion | `remap-addresses` |
| Register area omitted or mixed | `normalize-map` |
| Duplicate and overlapping points | `check-map` |
| Unknown byte and word order | `check-byte-order` |
| Too many individual polls | `plan-reads` |
| Recreating tool setup by hand | Target generator skills |
| One map must feed several tools | `build-tool-pack` |
| Stale, flat, missing, or implausible values | `analyze-capture` |
| Register maps change between firmware revisions | `compare-maps` |
| Manual tables lose source evidence | `parse-map` and `review-evidence` |
| Coil and packed-bit order confused with byte order | `check-map` |
| Unit identifier zero / broadcast risk | `check-map` |
| Illegal address or block boundary | `plan-reads` |
| Malformed or short Modbus response | `analyze-capture` |

Before adding a new skill, add the problem, common user phrasing, source links, a rights state, a synthetic fixture, and an observable acceptance test.
