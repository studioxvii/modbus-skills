# Addressing Conventions and Native Verification

This note explains the address numbering each read-only target uses and gives a
reproducible, loopback-only recipe for natively verifying the Modpoll and
ModScan artifacts. It exists because the most likely operator mistake is an
address-base mismatch: the generated files are faithful to the tool they target,
but a device addressed under a different convention will return the wrong
register or a Modbus exception.

## Address conventions by artifact

The canonical map and read plan are the source of truth. Every downstream file
is derived from them.

- Canonical map / read plan: `protocol_offset` is the 0-based Modbus PDU address.
  The read function code is fixed by register area:
  - coil -> FC01
  - discrete-input -> FC02
  - holding-register -> FC03
  - input-register -> FC04
- gavinying modpoll config (`<route>.csv`): uses traditional reference numbering
  added to the PDU offset. The bases are coil `0`, discrete-input `10000`,
  input-register `30000`, holding-register `40000`. gavinying modpoll sends these
  numbers literally on the wire without subtracting a base, which matches its own
  documented `examples/modsim.csv`. The device or simulator must therefore answer
  at the same traditional addresses.
- ModScan read plan (`read-plan.csv`): exposes both `protocol_offset_base_0` and
  `common_reference_base_1`. Enter the column that matches the addressing mode of
  your ModScan setup; the two describe the same point.
- pymodbus fallback (`pymodbus-read-once.py`): sends the 0-based PDU
  `protocol_offset` directly.

Operator caution: confirm which base your device or tool expects before trusting
a value. In Modicon numbering, holding reference `40001` is PDU offset `0`.

## Native verification recipe (loopback only)

These tools are external and are not runtime dependencies of this repository;
install them only when you want to natively verify an artifact:

```
python3 -m pip install pymodbus modpoll
```

Run a read-only synthetic device bound to loopback. It seeds the same values at
both the traditional wire addresses gavinying modpoll uses and the 0-based PDU
offsets the pymodbus fallback uses, so a correct read from either tool yields the
same numbers. pymodbus 3.9 maps a read of wire address `A` to datastore index
`A + 1`, so values are seeded at `A + 1`.

```python
import asyncio
from pymodbus.datastore import (
    ModbusSequentialDataBlock,
    ModbusServerContext,
    ModbusSlaveContext,
)
from pymodbus.server import StartAsyncTcpServer

SIZE = 40100

def block(seed_by_wire_addr):
    values = [0] * SIZE
    for wire_addr, val in seed_by_wire_addr.items():
        values[wire_addr + 1] = val  # pymodbus 3.9: index = wire address + 1
    return ModbusSequentialDataBlock(0, values)

store = ModbusSlaveContext(
    # coil (FC01): base 0 -> wire 0; discrete-input (FC02): base 10000 -> wire 10005
    co=block({0: 1}),
    di=block({5: 1, 10005: 1}),
    # input (FC04): base 30000 -> wire 30000; also seed PDU offset 0 for the fallback
    ir=block({0: 0x0001, 1: 0x86A0, 30000: 0x0001, 30001: 0x86A0}),
    # holding (FC03): base 40000 -> wire 40000/40002; also seed PDU offset 0/2
    # flow_rate float32 42.0 stored word-swapped (CDAB) as [0x0000, 0x4228]
    hr=block({0: 1234, 2: 0x0000, 3: 0x4228,
              40000: 1234, 40002: 0x0000, 40003: 0x4228}),
)
context = ModbusServerContext(slaves={1: store}, single=False)

async def main():
    print("read-only Modbus TCP simulator on 127.0.0.1:5020 (unit 1)")
    await StartAsyncTcpServer(context, address=("127.0.0.1", 5020))

if __name__ == "__main__":
    asyncio.run(main())
```

With the simulator running, verify Modpoll against its generated config:

```
modpoll --once --tcp 127.0.0.1 --tcp-port 5020 -f <route>.csv
```

Verify the shared fallback (this also covers ModScan on non-Windows hosts, since
ModScan itself is a Windows-only GUI) for any compiled request:

```
python3 pymodbus-read-once.py --request read-0001 \
    --host 127.0.0.1 --port 5020 --unit 1 --confirm-read READ
```

The fallback refuses to read unless `--confirm-read READ` is passed, and it only
issues FC01 through FC04. Keep every native check on loopback, bounded, and
read-only; a live device still requires one scoped human confirmation.
