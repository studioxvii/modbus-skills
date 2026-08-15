# Skill usability campaign

This directory is the v1 representative campaign for human-like skill use.

It does not call a model during normal repository verification. Deterministic
mode uses a fake session adapter and a scripted worker that speaks only from
scenario facts, then scores artifacts and events with hard oracles.

## Commands

Contract and fake-session campaign (no credentials):

```bash
python3 scripts/run_skill_usability_tests.py \
  --output artifacts/skill-usability
```

Explicit real-model campaign (Codex app-server; missing tools become `not-run`):

```bash
python3 scripts/run_skill_usability_tests.py \
  --mode real-model \
  --output artifacts/skill-usability-real
```

Raw transcripts stay under the chosen ignored `artifacts/` or `private/` root.
Shareable JSON and Markdown reports contain only generic IDs, issue codes,
counts, hashes, and statuses.
