# Post: a 32-bit Modbus value has more than one honest reading

Status: draft. Do not post until you have reviewed it.

Suggested places: a thread about swapped floats, FluentModbus-style endian questions, commissioning notes, r/PLC. Show the layouts. Do not claim the tool picked the winner.

Do not say this is listed in a marketplace unless that listing is live.

---

If a 32-bit flow rate looks like garbage, the register number may be fine. The word and byte layout may not be.

Manuals are sloppy about this. Some say “float.” Some say “CDAB.” A lot say nothing. Filling in ABCD because that is what the last drive used is how you commission the wrong engineering value.

I want the opposite: one raw sample, every supported layout on the table, and a human pick. No winner until someone confirms it.

The compile path does the same thing up front. If Flow Rate is FLOAT32 and the table leaves byte order blank, the run stops. It does not invent CDAB so the job can look finished.

Repo:

https://github.com/studioxvii/modbus-skills

If you already have the raw words:

```text
$check-byte-order Evaluate every supported layout for these raw words.
Do not choose a winner.
```

If you only have the manual:

```text
$compile-user-map Use this local map. If byte order is missing, stop and tell me.
```
