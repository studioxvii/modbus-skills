# Build Modpoll (BETA)

Build deterministic read-only BETA artifacts for gavinying/modpoll or supported Witte Modbus Poll profiles.

## Use this when

The user asks for Modpoll, Witte Modbus Poll, gavinying CSV, or a Modpoll probe/final setup.

## What you get back

- The profile folder under `modpoll/` - Start here. It contains the CSV, XML, or command files used to configure the selected Modpoll product.
- The profile `README.md` - Short operator instructions for those files.
- `pymodbus-read-once.py` - Optional cross-platform FC01-04 fallback. It requires one compiled request, endpoint, port, and matching unit ID.

## Example request

Generate a Witte Modbus Poll setup from this map.

## Safety boundary

This skill does not write registers, force coils, broadcast, scan a network, or start unbounded polling. Unresolved engineering fields stay visible.

[View the skill source on GitHub](https://github.com/studioxvii/modbus-skills/blob/main/plugins/modbus-skills/skills/build-modpoll/SKILL.md)
