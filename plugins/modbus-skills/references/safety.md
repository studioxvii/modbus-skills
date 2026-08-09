# Safety Contract

- Generate reads only with function codes 01, 02, 03, and 04.
- Do not generate function codes 05, 06, 15, 16, 22, or 23.
- Do not use Unit Identifier 0 for a broadcast.
- Do not discover or scan addresses.
- Bound quantity, interval, retries, and run duration.
- Keep generated flows and probes disabled until one scoped preflight confirms endpoint
  values immediately before live use.
- Stop final generation on unresolved engineering fields.
- Do not require human approval for deterministic local transformations or checks.
