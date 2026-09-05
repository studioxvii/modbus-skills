# OEM corpus file setup

Prepares ignored working files under `private/oem-corpus/`. Does **not** run skills.

## One command

From the repository root:

```bash
python3 scripts/setup_oem_corpus_files.py
python3 scripts/build_oem_synthetic_fixtures.py   # only if synthetic/ changed
```

## What gets created

```
private/oem-corpus/
  sources-manifest.json          # download/copy report + SHA-256
  synthetic/                     # mirror of tests/fixtures/oem-corpus/synthetic/
  <asset-id>/
    request.json                 # compile-user-map stub (all-readable)
    source/
      <oem file>                 # PDF, XLSX, or CSV
```

## Per-asset sources

| Asset | Primary file in `source/` | Notes |
| --- | --- | --- |
| caterpillar-gen | `emcp4-scada-data-links.pdf` | Downloaded |
| cummins-gen | `modbus-register-mapping-a029x159.pdf` | Downloaded |
| generac-gen | `generac-dg-0d2901rev0.pdf` | Downloaded + optional Omnimetrix IFs |
| tesla-bess | `tesla-powerpack-site300-integration.pdf` | Downloaded (partial map) |
| gotion-bess | `gotion-ess-manual-eng.pdf` + `register-map-integrator.xlsx` | PDF downloaded; XLSX from synthetic |
| narada-bess | `register-map.csv` | Synthetic working source until OEM PDF acquired |
| veris-meter | `register-map.csv` | Synthetic until E51 guide downloaded |
| shark-270 | `shark-270-modbus-protocol-guide-e159718.pdf` | Downloaded |
| sungrow-pv | `sungrow-string-inverter-protocol-v1.1.37.pdf` | Downloaded |
| schneider-pm5560 | `PM556x_PublicModbusRegisterList_10th_August-23.xlsx` | Downloaded from Schneider FAQ |
| huawei-sun2000 | `huawei-sun2000-modbus-interface-v3.pdf` | Downloaded |

Tracked metadata lives in `manifest.json`. Tracked messy fixtures live in `synthetic/`.

## Windows lab files

See the [ModScan native-verification guidance](../../../plugins/modbus-skills/skills/build-modscan/references/native-verification.md).
Keep machine-specific VM setup outside the public plugin; it is not required for
offline source compilation.

## After setup

- Compile outputs go to `artifacts/oem-corpus/<asset-id>/` (also ignored).
- Do not commit anything under `private/` or `artifacts/`.
