# User map

## Included

### all documented Modbus read points
- `tank_level` — Tank Level (holding-register offset 0)
- `flow_rate` — Flow Rate (holding-register offset 2)
- `energy_total` — Energy Total (input-register offset 0)

## Suggestions
- None

## Blocking exceptions
- None

## Exclusions and evidence annex
- point.write-only-not-readable: The source declares a write-only point. It cannot enter a read plan.
- level_setpoint: Excluded because the OEM map marks this point write-only.
