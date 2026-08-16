# Compile example

This folder is a rights-safe, fictional commissioning table. Names, addresses,
and values are made for this example.

The completed run turns a mixed 40001/30001 table into protocol offsets and
records the write-only setpoint as an exclusion. It maps 40001 to holding-register
offset 0, 40003 to holding-register offset 2, and 30001 to input-register offset
0. Its multi-register rows already state CDAB and ABCD, so it has no blocking
byte-order decision.

A separate unresolved run leaves byte order blank for Flow Rate and Energy
Total. That run pauses so you can confirm the layout.

## Files

| File | Role |
| --- | --- |
| `source.csv` | Completable source: byte order is stated for multi-register points |
| `source-unresolved.csv` | Same table with byte order left blank |
| `request.json` | `all-readable` compile of `source.csv` |
| `request-unresolved.json` | Same request against the unresolved table |
| `output/user-map.md` | Human summary from the completable run |
| `output/user-map.csv` | Spreadsheet export from the completable run |
| `output/user-map.json` | Machine-readable map from the completable run |

## Reproduce from the repository root

```bash
python3 plugins/modbus-skills/skills/compile-user-map/scripts/run.py \
  --request docs/examples/compile-user-map/request.json \
  --output /tmp/modbus-skills-example-compile
```

The unresolved request pauses for a source decision. That is the intended result.

```bash
python3 plugins/modbus-skills/skills/compile-user-map/scripts/run.py \
  --request docs/examples/compile-user-map/request-unresolved.json \
  --output /tmp/modbus-skills-example-unresolved
```
