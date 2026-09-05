# Modpoll Profiles (BETA)

## `gavinying-cli`

Generate the documented open-source `device`, `poll`, and `ref` CSV collections.
Use with the gavinying/modpoll CLI, not proconX FieldTalk modpoll.
Both `poll` and `ref` addresses are zero-based PDU offsets. Do not add a
30000/40000 register-area prefix; the CSV area field already selects the table.
Final scalar coil/discrete-input values are unsupported by this profile's
byte-group decoder. Single-attempt probe mode is also unsupported: `--once`
still permits internal retries. Keep these targets held and offer proconX or
Node-RED for a one-attempt probe. Never present byte groups as scalar bits.
Omit identity multipliers to preserve large integer precision. Zero multipliers
and nonidentity 64-bit integer scaling remain held because this client cannot
preserve their documented values.

Final commands use the generated `<route>-read-final.py` launcher, not bare
`modpoll --once`. It invokes the native CLI once with documented `--export`,
unchanged polling/retry defaults and an exact private copy of the bound CSV.
Gavinying1.6.0 console floats are rounded to three decimal places; the primary
engineering output is the launcher's validated full-precision `result.json`.
Identity integers remain exact JSON integers (including uint64); scaled integers
are validated as their floating-point engineering result. Identity strings retain
bounded string validation. Existing unsupported semantic holds are unchanged.

The standalone launcher requires Python3.11+ and POSIX directory-fd/O_NOFOLLOW
filesystem primitives; missing safe-publication support stops before connection.
It rejects config/name collisions, symlinks, foreign output ownership and
concurrent/stale locks. A fresh private staging export prevents stale-file reuse;
complete expected keys and finite typed nonnull values are mandatory. Native
exit0 does not imply a read/export succeeded. One fixed atomic status-and-values
envelope replaces running/failed/succeeded state; running or failed envelopes
never contain prior values. A publication failure reports failure without
claiming an existing result is current. Stdout is a compact invocation receipt,
not values/native diagnostics. Require successful exit, `published=true` and
`status=succeeded`, then match its `run_id`, `binding_sha256` and status to the
result envelope. Argument/preflight/lock failures can leave another or prior
result untouched; `published=false` never makes it current. A retained file's
`values_current` alone is not freshness evidence.
Only the ownership marker and latest envelope are retained after owned staging
cleanup, not timestamped histories. A changed compiled setup requires a fresh
explicitly selected output directory. Never remove an unknown owner's lock/file.

The manifest declares an external per-route wall-time cap (maximum300seconds);
stdout/stderr and JSON are byte-bounded. Caps are not proof of native stopping.
The launcher neither adds requests nor makes `--once` a single-attempt probe.
One ordinary SIGTERM/SIGINT cleans up the owned child and publishes failure when
possible. SIGKILL/power loss cannot guarantee cleanup: retained lock/staging
fails closed and must not be removed by guessed ownership.
Native verification of a generated launcher remains not-run until its exact
bytes have a separate native receipt; generation alone grants no native proof.

## `proconx-cli`

Generate one bounded proconX FieldTalk `modpoll` command per compiled read block.
Use when the installed tool is proconX modpoll from modbusdriver.com. This profile
does not emit gavinying CSV files.
Use `-0` for PDU addressing in every area. Final blocks must contain one datatype,
with no gaps or partial values. The command count is typed values, not words.
Unsupported types or mixed blocks remain held; probe mode reads raw words.
Nonidentity multipliers and additive engineering offsets remain held in final
mode because this client does not apply those transforms. A raw probe must not
be presented as scaled engineering values.
Do not copy gavinying datatype strings into proconX command flags.

## `witte-desktop`

Generate readable plans and bounded PowerShell automation. `live_read_seconds` defaults to 10 and must be from 1 through 30. Each read interval must be at least 1000 ms. One route must not exceed five aggregate read requests per second. Generation stops when either polling limit fails. The operator must type `READ` before a connection opens.

Start with no existing Modbus Poll process. Configure and disable every document
before opening a connection so default requests cannot escape. Probe mode uses
the documented disabled-document `ReadWriteOnce` operation once per block; final
mode enables only the configured documents for the bounded interval.

Final mode supports only single-register `uint16` FC03/04 values with identity
scale/engineering offset and unchanged byte layout. The script explicitly uses
the documented Automation `SetFormat(index, 1)` unsigned display before saving
or connecting; index zero is the first address in the document. Signed, multiword,
float, string, bitfield, scalar-bit, swapped-layout, and transformed final values
remain held. Offer an explicitly raw probe or another supported final profile.
Raw probe mode does not configure datatype display, layout, or engineering
transforms. Wire success is not proof of engineering display. Generation and
native verification remain separate; no per-artifact native check is implied.

## `witte-v12-xml`

Generate disabled, human-readable XML read documents using Witte's published
version-12 structure and the native `.mbp` filename extension. Do not store
connection settings. Modbus Poll 13.2.1 accepts the `.mbp` document but does not
open byte-identical content named `.xml`; verify other installed versions separately.

This profile currently supports raw probes, not decoded final values. The sample
XML `f=0` has no established display-enum contract here; do not infer the
Automation format enum or unsigned/Boolean/float interpretation. Final requests
remain held with a raw-probe alternative. Probes configure neither datatype
display nor byte-layout conversion, scale, or engineering offset.

Stored `Data/Bytes/B` entries number one per coil/discrete input for FC01/02 and
two per register for FC03/04. These are XML storage entries, not the packed
Modbus response-byte count. Generated documents remain disabled.

The XML `.mbp` content is documented text, not an invented opaque binary. Neither
profile synthesizes an opaque `.mbw` workspace. The XML profile is not
interchangeable with Witte desktop automation.
