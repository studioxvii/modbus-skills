---
name: ask-modbus
description: Route an unclear Modbus engineering goal to one focused workflow. Use when the request does not provide enough information to select a focused skill. Also use as the safety stop for requests that ask for Modbus writes, broadcasts, network discovery, or unbounded polling. Do not use as the primary skill when a safe request names a concrete input, conversion, check, analysis, or target output that matches another skill.
---

# Ask Modbus

Identify the user's goal. Route to one focused skill. Do not solve a fragile conversion from memory when a deterministic skill covers it.

## Route

- Use `parse-modbus-map` for CSV, JSON, XML, XLSX, or structured text.
- Use `extract-modbus-map-from-pdf` for manuals and PDF register tables.
- Use `normalize-modbus-map` to represent mixed or unresolved source fields without a map-wide conversion.
- Use `lint-modbus-map` for deterministic checks on an already normalized map.
- Use `diagnose-modbus-map` for end-to-end cleanup of a raw, imported, or messy map.
- Use `review-modbus-evidence` to classify confirmed, inferred, unresolved, and rejected evidence.
- Use `remap-modbus-addresses` to convert a map between known address conventions.
- Use `evaluate-modbus-byte-order` for raw-word interpretation.
- Use `capture-modbus-sample` for bounded sample acquisition when raw words must be collected first.
- Use `compile-modbus-read-plan` to group points into bounded reads.
- Use a named target generator to render Node-RED, Modpoll, Modbus Poll, or ModScan output from an existing canonical map and plan.
- Use `build-modbus-tool-pack` for several outputs or an undecided target.
- Use `analyze-modbus-capture` for recorded or bounded live data.
- Use `compare-modbus-maps` for device or firmware revisions.

Ask only for information that blocks the selected workflow. Stop on requests for writes, broadcasts, network discovery, or unbounded polling.
