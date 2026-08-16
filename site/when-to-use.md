# When to use Modbus Skills

Use this when you need a usable map, a layout check, or a read-only polling setup from vendor documentation.

## This work

- You have a vendor PDF, spreadsheet, CSV, JSON, XML, or text register map.
- You need a readable map plus JSON and CSV.
- A 32-bit or 64-bit value looks wrong and you want every possible layout from one sample so you can choose.
- A firmware or device map changed and you need to see what was added, removed, moved, or changed.
- You want a read-only Node-RED, Modpoll (BETA), or ModScan (BETA) setup from a map you have already checked.

Start by asking for a user map from the vendor file. If you want help choosing the next step, ask for that.

## Other work

- Register writes, coil forces, and broadcasts.
- Network discovery and unbounded polling.
- Choosing a byte order, unit ID, or address style while the manual is still silent.
- Opening a live device while the manual is still being read. Live reads come later, as a limited step.

Unclear documentation becomes a short list of questions. Byte order, 40001 conversions, and write-only points wait for a stated value or an explicit exclusion.

See [the worked example](examples/compile-user-map.html).
