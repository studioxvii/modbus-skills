# OEM register-map corpus

Energy-asset Modbus documentation for compile, usability, and Windows native-tool
verification. This directory is **metadata only**. Original vendor files live under
ignored `private/oem-corpus/`; compile outputs go to ignored `artifacts/oem-corpus/`.

## Format mix (target)

| Format | Target | Current assigned |
| --- | --- | --- |
| PDF | 4+ | 7 |
| XLSX | 2+ | 2 |
| CSV | 2+ | 3+ (8 parseable synthetic + intake junk) |

## Assets

| ID | Asset | Format | Status |
| --- | --- | --- | --- |
| `caterpillar-gen` | Caterpillar EMCP | PDF | sourced |
| `cummins-gen` | Cummins PowerCommand | PDF | sourced |
| `generac-gen` | Generac DG / G / H / Evolution | PDF | sourced |
| `tesla-bess` | Tesla Powerpack / Megapack | PDF (partial) | blocked |
| `gotion-bess` | Gotion BESS / BMS | XLSX | sourced (synthetic surrogate) |
| `narada-bess` | Narada NPFC / NESP | CSV | sourced |
| `veris-meter` | Veris E51 / E54 | CSV | sourced |
| `shark-270` | EIG Shark 270 | PDF | sourced |
| `sungrow-pv` | Sungrow string inverter | PDF | sourced |
| `schneider-pm5560` | Schneider PM5560 meter | XLSX | sourced |
| `huawei-sun2000` | Huawei SUN2000 inverter | PDF | sourced |

## Synthetic messy fixtures (tracked)

Apache-2.0 integrator-style exports in `synthetic/` — intentionally imperfect for parser
and compile testing. See `synthetic/README.md`.

- **`parseable-messy/`** — records parse with warnings (8 files + 1 XLSX)
- **`intake-junk/`** — title rows and bad header aliases (4 files)

Regenerate: `python3 scripts/build_oem_synthetic_fixtures.py`

See `manifest.json` for URLs, license notes, and local paths.

Full program: `docs/plans/2026-08-25-001-modbus-skills-optimization-plan.md`.

## File setup (no skill runs)

```bash
python3 scripts/setup_oem_corpus_files.py
```

Downloads OEM PDFs/XLSX into ignored `private/oem-corpus/`, copies synthetic working
CSVs/XLSX, and writes per-asset `request.json` stubs. Details: `SETUP.md`.

## Redistribution

Per `docs/testing.md`, do not commit vendor manuals or complete register maps unless
redistribution is documented. Tracked files here are manifests and README stubs only.
Extracted CSV/XLSX derivatives need attribution and a human review record in each
asset README.

## Workflow

```bash
# 1. Download source (example)
mkdir -p private/oem-corpus/sungrow-pv/source
curl -L -o private/oem-corpus/sungrow-pv/source/protocol.pdf \
  'https://www.photovoltaicsolar.in/Sungrow_Manual/Modbus%20RS485%20RTU%20Protocol.pdf'

# 2. Create request.json under private/oem-corpus/<id>/

# 3. Compile
python3 plugins/modbus-skills/skills/compile-user-map/scripts/run.py \
  --request private/oem-corpus/sungrow-pv/request.json \
  --output artifacts/oem-corpus/sungrow-pv
```

## Anchors (verification priority)

1. **PDF:** `sungrow-pv` — clear public protocol, offset notation stress
2. **CSV:** `veris-meter` — after derivative extraction from E51 point map
3. **XLSX:** `gotion-bess` — pending OEM/integrator map
