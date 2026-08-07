---
name: build-modbus-tool-pack
description: Build Node-RED, Modpoll, ModScan, or any non-empty combination from one reviewed Modbus map and one compiled read plan. Use when a user wants several tool outputs, a portable device pack, or is unsure which target to choose.
---

# Build Modbus Tool Pack

Invoke only the selected target adapters. Keep every output tied to the same map and read-plan hashes.

## Workflow

1. Require at least one target.
2. Validate the canonical map.
3. Compile or verify the shared read plan.
4. If required byte order is unresolved, offer a `probe` pack and hold final generation.
5. Run `python3 <skill-dir>/scripts/run.py --request <tool-pack-request.json> --output <directory>`.
6. Report each target as `generated`, `held`, `unsupported`, or `verification-failed`.
7. Verify each selected target independently.

Support all seven non-empty combinations of Node-RED, Modpoll, and ModScan. A failed target must not hide successful outputs. Never generate a target that the user did not select.

Read `references/tool-pack.md` for the request and manifest contracts.
