# Native verification

Generated CSV files describe settings to enter in ModScan32 or ModScan64; they are
not native configuration or import formats. A generated plan, screenshot, or
successful PyModbus fallback does not establish that ModScan executed it correctly.

## Tested address-entry scope

ModScan64 6.0.0.4 uses a one-based **Point Address** field, distinct from both
the zero-based protocol offset and the common reference label. Independent
loopback wire checks verified Point Address 1 → protocol offset 0 and Point
Address 65535 → protocol offset 65534 for FC03. The UI rejects 0 and 65536.
Use the generated `modscan_point_address_base_1` value; do not calculate it by
editing the canonical map. A request starting at protocol offset 65535 is held
until an alternative native entry path is verified. Do not transfer this version's
receipt to ModScan32 or other versions.

Bounded FC03 raw-word reads do not verify engineering display layouts, other
function codes, native retry limits, or single-attempt probe behavior. An external
simulator's request cap is not a native polling bound.

## Receipt requirements

Use only an authorized read envelope. For automated tests, use a finite independent
loopback simulator, label the actor `test-harness`, and record that no human device
approval was given. Do not substitute that test authorization for permission to
access a real device. Never discover endpoints, write registers, or poll indefinitely.

Retain one machine-readable diagnostic receipt with:

- Installed application name/version and the exact tested mode (manual entry,
  bounded read, or display interpretation).
- Hashes of the canonical map, read plan, generated files, simulator definition,
  and immutable capture or application-saved evidence.
- Expected route/unit, FC01–04, zero-based protocol offset and quantity; the exact
  observed request identity, request count, duration and cleanup result.
- Expected and observed raw words/bits and, when tested, datatype/layout, scale and
  decoded values. A raw-word test cannot certify engineering-value display.
- Per-check `passed`, `failed`, `blocked`, or `not-run`, with reasons and evidence
  paths. Scope and version limits remain explicit; do not promote untested modes.

Verify physical requests independently of the application's status display. For a
probe, a retry is a second request and must not be hidden behind “read once.” Keep
failed receipts and do not edit a generated manifest's `not-run` state merely because
an unrelated client worked. Summarize only the verified scope in the user handoff;
raw logs and detailed receipts are diagnostics, not additional default deliverables.
