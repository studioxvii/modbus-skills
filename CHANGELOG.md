# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

### Security

- Read-only generation only: no writes, broadcasts, discovery scans, stored
  credentials, or unbounded polling.

[0.2.0]: https://github.com/studioxvii/modbus-skills/tree/main
