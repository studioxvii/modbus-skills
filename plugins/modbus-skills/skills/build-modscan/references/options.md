# ModScan Options (BETA)

Adapter version 1.0.0 emits a version-neutral plan. It lists ModScan32 and ModScan64 as native verification targets.

This target is BETA. The output does not prove verification in either product. Verify the generated plan in the named installed application.

The command exposes `--options`, but adapter version 1.0.0 defines no supported option keys. Do not pass `--options`.

Supported outputs are:

- `read-plan.csv`
- `test-message-plan.csv`
- `point-map.csv`
- manifests
- operator instructions

Do not invent `.tst` or `.cfg` data. Do not claim a native import format.
