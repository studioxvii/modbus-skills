# Synthetic OEM fixtures

Apache-2.0 fictional register maps styled after real energy assets. Safe to commit.
They deliberately use **imperfect structure** so skills are tested against integrator
exports, not clean canonical CSV.

Regenerate everything:

```bash
python3 scripts/build_oem_synthetic_fixtures.py
```

## Two tiers

### `parseable-messy/`

Header on row 1, but non-canonical column names and datatype spellings. The deterministic
parser should yield records (with warnings).

| File | Paired asset | Stress |
| --- | --- | --- |
| `veris-meter-messy.csv` | Veris E51 | `display_address`, mixed float32 casing |
| `narada-bess-messy.csv` | Narada BESS | `protocol_offset` instead of 4xxxx |
| `generac-dg-messy.csv` | Generac DG | Low offsets like F-panel maps |
| `cummins-pc-messy.tsv` | Cummins PC | Tab-separated, paired 32-bit registers |
| `sungrow-inverter-messy.csv` | Sungrow PV | Input-register area labels |
| `cat-emcp-messy.csv` | CAT EMCP | Modicon `Register` column |
| `schneider-pm5560-messy.csv` | Schneider PM5560 | Float32/int64 naming |
| `tesla-bess-messy.csv` | Tesla BESS | Partial site-controller slice |

### `intake-junk/`

Title rows, `#` comments, or header aliases the parser does not recognize (`Modbus Addr`,
`Hex Addr`, `Doc Register`). Expect **zero or rejected rows** from naive `parse-map`;
`compile-user-map` should still recover via bounded intake, not row-by-row prompts.

| File | Paired asset | Stress |
| --- | --- | --- |
| `veris-meter-export-junk.csv` | Veris | Comment preamble + `Modbus Addr` |
| `narada-bess-junk.csv` | Narada | Bilingual title rows + hex column |
| `sungrow-inverter-junk.csv` | Sungrow | Register-minus-1 note + dual address cols |
| `cummins-title-junk.csv` | Cummins | Document title before header |

### Root XLSX

`gotion-bess-integrator-messy.xlsx` — multi-sheet integrator export:

- `Cover` — project metadata (noise sheet)
- `BMS_Map` — real map with `Register` + `Tag Name` + mixed EN/ZH comments
- `Alarms` — non-map sheet the parser should skip or reject

## Design rules

1. **Never perfectly canonical** — avoid the exact header row from `docs/examples/compile-user-map/source.csv` unless testing a control case.
2. **Mix address notations** — `protocol_offset`, `display_address`, Modicon refs, and hex in different files.
3. **Mix delimiters** — CSV, TSV, and XLSX in the same corpus.
4. **Pair with OEM docs** — each file maps to a real asset in `manifest.json` for compile intent and verification.

## Expected parser smoke test

```bash
python3 -c "
from pathlib import Path
import sys
sys.path.insert(0, 'plugins/modbus-skills/runtime')
from modbus_skills.parsers import parse_source
root = Path('tests/fixtures/oem-corpus/synthetic/parseable-messy')
for path in sorted(root.glob('*')):
    n = len(parse_source(path.read_bytes(), filename=path.name)['records'])
    assert n > 0, path.name
print('parseable-messy OK')
"
```
