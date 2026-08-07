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

The manifest records adapter versions, assumptions, holds, generated paths, and input hashes. Deterministic files must not contain timestamps or local absolute paths.
