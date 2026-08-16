# Post: 40001 is not the address the wire uses

Status: draft. Do not post until you have reviewed it.

Suggested places: r/PLC, a commissioning Slack, LinkedIn, a Node-RED thread about register addresses. Help with the addressing problem first. Link second.

Do not say this is listed in a marketplace unless that listing is live.

---

A vendor table says Tank Level is at 40001. A Python client wants address 0. Both can be right.

Modbus PDUs are zero-based. A lot of manuals still print 3xxxx / 4xxxx reference numbers. Mix those in one spreadsheet and you poll the wrong register, or you think two maps disagree when they only disagree about notation.

I keep a read-only workflow for this. It keeps 40001 as the source register, writes protocol offset 0, and will not treat the display number as the PDU address.

On a synthetic four-row table it did this:

- 40001 Tank Level → holding-register offset 0
- 40003 Flow Rate → holding-register offset 2
- 30001 Energy Total → input-register offset 0
- 40010 Level Setpoint → excluded, write-only, not a read

If you have a real manual that mixes those notations, the repo is here:

https://github.com/studioxvii/modbus-skills

Prompt:

```text
$compile-user-map Use this local map to create a user map for the points I poll.
Return Markdown, JSON, and CSV. Do not guess address notation.
```

The public example is synthetic on purpose. Use your own file.
