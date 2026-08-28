# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.2] - 2026-08-28

### Fixed

- Recover XLSX intake when the real header sits under a worksheet title row
  (EMCP-style workbooks).
- Parse ComAp-style Entergy register workbooks without collapsing address
  columns into junk candidates.
- Keep multi-table and duplicate-label XLSX maps compiling instead of aborting
  the whole map (ASCO / EGCP-2 layouts).
- Compile Schneider PM5560 public register lists without point-ID collisions;
  recognize Schneider datatype/access tokens and skip only explicit `---`
  placeholder rows.
- Parse Gotion BESS integrator XLSX into user-map artifacts and track OEM
  synthetic fixtures so CI can exercise the corpus gates.
- Reset compile-workflow output before each run so repeated
  `compile-user-map` checks stay idempotent.
- Align compare-maps usability gate keywords with the live test names.
- Document Windows lab receipts for ModScan CSV entry and add a ProconX CLI
  profile for Windows-lab Modpoll generation.

## [0.2.1] - 2026-08-21

### Fixed

- Make compile-user-map case artifact writes portable on Windows by closing the
  temporary file before replace/cleanup and tolerating missing `os.fchmod`.
- Keep Claude plugin skill packaging LF on Windows so variant validation no
  longer rejects every Claude skill for CRLF adapter metadata.

### Changed

- Clarify that `capture-sample` generates a probe pack and stops before the live
  read; `capture.json` is created by the operator or enabled tool afterward.
- Disclose that unit identifiers stay in `1..247` in this release: unit `0`
  remains broadcast-forbidden, and TCP gateway IDs `0`/`255` are not accepted.

## [0.2.0] - 2026-08-15

### Added

- Twenty read-only Modbus skills covering map compile, review, byte-order,
  bounded read plans, tool packs, and capture analysis.
- Portable plugin packages for Codex, Cursor, Claude Code, and other Agent
  Plugins 1.0 clients.
- Deterministic runtime, synthetic fixtures, and repository verification.
- Representative skill-usability campaign with fake-session CI coverage.
- Generated documentation site, research catalog, and workflow indexes.

### Changed

- Label Modpoll, Witte Modbus Poll, and ModScan exports as BETA until native
  application verification.
- Pin setuptools package discovery so `python3 -m pip install -e .` works from a
  fresh checkout.

### Security

- Read-only generation only: no writes, broadcasts, discovery scans, stored
  credentials, or unbounded polling.

[0.2.2]: https://github.com/studioxvii/modbus-skills/releases/tag/v0.2.2
[0.2.1]: https://github.com/studioxvii/modbus-skills/releases/tag/v0.2.1
[0.2.0]: https://github.com/studioxvii/modbus-skills/releases/tag/v0.2.0
