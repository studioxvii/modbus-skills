# Map-matrix WORKER

Workers are **deterministic**. Prefer `run_worker.py` over ad-hoc exploration.

## Job

1. Claim one pending map (`program_ctl.py claim`).
2. Run `run_worker.py --map-id <id>`.
3. On fail → call `run_improve.sh` (Improver model).
4. Finish status in `program.json`.

## Do not

- Invent scores.
- Commit vendor maps.
- Run more than one map at a time per worker process.
- Call sudo.

Model for this role: **composer-2.5-fast** (or Grok 4.6 fast). The CLI is the source of truth.
