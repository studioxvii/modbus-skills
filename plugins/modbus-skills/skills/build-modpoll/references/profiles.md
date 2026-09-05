# Modpoll Profiles (BETA)

## `gavinying-cli`

Generate the documented open-source `device`, `poll`, and `ref` CSV collections.
Use with the gavinying/modpoll CLI, not proconX FieldTalk modpoll.
Both `poll` and `ref` addresses are zero-based PDU offsets. Do not add a
30000/40000 register-area prefix; the CSV area field already selects the table.

## `proconx-cli`

Generate one bounded proconX FieldTalk `modpoll` command per compiled read block.
Use when the installed tool is proconX modpoll from modbusdriver.com. This profile
does not emit gavinying CSV files.
Use `-0` for PDU addressing in every area. Final blocks must contain one datatype,
with no gaps or partial values. The command count is typed values, not words.
Unsupported types or mixed blocks remain held; probe mode reads raw words.
Do not copy gavinying datatype strings into proconX command flags.

## `witte-desktop`

Generate readable plans and bounded PowerShell automation. `live_read_seconds` defaults to 10 and must be from 1 through 30. Each read interval must be at least 1000 ms. One route must not exceed five aggregate read requests per second. Generation stops when either polling limit fails. The operator must type `READ` before a connection opens.

## `witte-v12-xml`

Generate disabled XML read documents for Witte version 12 or later. Do not store connection settings.

Neither Witte profile creates opaque `.mbp` or `.mbw` project data. The XML profile is not interchangeable with Witte desktop automation.
