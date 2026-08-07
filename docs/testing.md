# Test Strategy

## Fast tests

- Address conversion and boundary tests.
- Composite identity tests.
- Byte-order vectors and IEEE edge cases.
- Parser round trips and rejected-row diagnostics.
- Map validation and comparison.
- Read-plan grouping and target limits.
- Adapter determinism and read-only safety.
- All seven non-empty target combinations.
- Skill metadata and activation fixtures.

## Public fixtures

Use synthetic fixtures that cover all four Modbus data areas, multiple unit identifiers, address boundaries, common datatypes, all declared byte layouts, gaps, overlaps, bits, strings, counters, and error cases.

Every non-synthetic fixture requires a rights record.

## Native acceptance

Golden files are not sufficient for final target approval.

- Import Node-RED flows into pinned Node-RED and Modbus node versions.
- Load the open-source Modpoll profile with the pinned implementation.
- Create and reopen Witte Modbus Poll artifacts through the licensed application.
- Load ModScan artifacts through the licensed application.
- Compare all target reads with one synthetic Modbus server.
