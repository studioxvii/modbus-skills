# Addressing conventions and native verification

Keep the canonical map and read plan unchanged when adapting notation for a tool.
`protocol_offset` is the zero-based address in the Modbus PDU. Register area selects
FC01 (coil), FC02 (discrete input), FC03 (holding register), or FC04 (input register).
A common reference such as holding register 40001 denotes protocol offset 0; it
is not the literal wire address.

| Output | Address to use |
| --- | --- |
| Gavinying config | Zero-based protocol offset. The read-function section supplies the area; do not add 10000, 30000 or 40000. |
| proconX commands | Generated `-0` makes `-r` the zero-based protocol offset. Preserve both flags. |
| Witte desktop/XML | Generated documents use zero-based addressing (`OneBased=0`). Preserve the generated mode and address together. |
| ModScan raw test message | Zero-based protocol offset encoded in the PDU. |
| ModScan64 6.0.0.4 Point Address UI | Generated `modscan_point_address_base_1`, equal to protocol offset + 1; not the common reference. Other versions require separate verification. |
| Shared PyModbus fallback | Zero-based protocol offset directly. |

See [ModScan native verification](../skills/build-modscan/references/native-verification.md)
for the tested UI range and its limits. A generated artifact's `not-run` native
state remains accurate until that exact artifact has its own receipt.

## Native test requirements

Use an independent finite loopback simulator with an explicit allowlist of unit,
function, zero-based offset and quantity. Reject every other request, including
writes and broadcasts. Set a wall-time bound and request cap, and clean up owned
processes/connections. Label automated authorization as `test-harness`, not human
device approval.

Seed only the expected protocol locations. Never seed the same values at both a
zero-based address and a reference-prefixed address: that would conceal an
addressing error. Verify exact wire requests independently of returned values and
application counters; record raw values separately from decoded display checks.

Retain application/version, input and generated-artifact hashes, simulator rules,
expected/observed wire identities, request counts, values and cleanup in local
diagnostic receipts. Keep failed runs and mark untested modes explicitly. Neither
installation, an open document nor a screenshot alone proves a valid read.

Gavinying `--once` limits polling passes but can retry internally; its probe output
is held because this does not prove a single attempt. A simulator request cap is
also not proof that a native application stops polling. Test retry and stopping
behavior separately.

The shared fallback can test one reviewed FC01–04 request, but its success does
not certify Gavinying, proconX, Witte, ModScan, or any other native profile. Do not
transfer receipts across products, versions, profiles or address/display modes.
Production-device access remains outside an isolated test authorization.
