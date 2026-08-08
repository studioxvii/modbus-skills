# Tool-Pack Contract

## Request

```json
{
  "mode": "final",
  "map": "canonical-map.json",
  "read_plan": "read-plan.json",
  "targets": [
    {"id": "node-red"},
    {"id": "modpoll", "profile": "witte-desktop"},
    {"id": "modscan"}
  ]
}
```

`mode` is `probe` or `final`. `targets` must contain at least one unique target.

## Output

```text
manifest.json
canonical-map.json
read-plan.json
validation.json
checksums.sha256
node-red/
modpoll/
modscan/
```

The manifest records adapter versions, assumptions, holds, generated paths, the exact source-map hash, the read-plan hash, and hashes for both portable projections. In `final` mode, the plan must be bound to the exact source-map hash.

The output file named `canonical-map.json` is a `modbus-runtime-map/v1` projection. It contains only target-visible runtime fields. The output `read-plan.json` contains only request, point-trace, original provenance, visible planning-option, and sanitized hold fields. It does not repair or replace stale provenance. Both files exclude review audit data, approval identities, source evidence, arbitrary plan metadata, and local source metadata. Deterministic files must not contain timestamps, credentials, or local absolute paths.
