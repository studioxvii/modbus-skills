# ModScan Options (BETA)

Adapter version 1.1.0 emits a protocol plan plus an explicit one-based Point Address
column tested with ModScan64 6.0.0.4. ModScan32 and other versions need separate verification.

This target is BETA. The output does not prove verification in either product. Verify the generated plan in the named installed application.

The command exposes `--options`, but adapter version 1.1.0 defines no supported option keys. Do not pass `--options`.

Supported outputs are:

- `read-plan.csv`
- `test-message-plan.csv`
- `point-map.csv`
- manifests
- operator instructions

Do not invent `.tst` or `.cfg` data. Do not claim a native import format.
