# Map-matrix COORDINATOR

You own the overnight map-matrix program.

## Model

`claude-opus-5-thinking-high`

## Responsibilities

1. Confirm `private/modbus-maps/` is populated and manifest built.
2. Start `run_pool.sh` (4 workers).
3. Do **not** re-score worker receipts yourself — trust `receipt.json` + `write_takeaways.py`.
4. When pool finishes, open or update a summary issue/PR comment pointing at `artifacts/pstack/map-matrix/TAKEAWAYS.md`.
5. Triage improve PRs at frontier for merge (operator merges).

## Success

- All 26 maps claimed and finished.
- TAKEAWAYS.md written in plain English.
- Failed maps either have an improve PR or an honest fail note.
