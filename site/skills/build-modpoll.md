# Build Modpoll (BETA)

Build deterministic read-only BETA artifacts for gavinying/modpoll or supported Witte Modbus Poll profiles.

## Use this when

The user asks for Modpoll, Witte Modbus Poll, gavinying CSV, or a Modpoll probe/final setup.

## What you get back

- The profile folder under `modpoll/` - Start here. It contains the CSV, XML, or command files used to configure the selected Modpoll product.
- The profile `README.md` - Short operator instructions for those files.
- Gavinying `<route>-read-final.py` - Bounded native launcher; one owned output directory retains a single atomic `result.json` status-and-values envelope. Stdout is a compact invocation receipt; values and bounded diagnostics stay in the envelope. Require successful exit and `published=true`, and match receipt `run_id`, `binding_sha256` and `succeeded` status to the envelope with `values_current=true`. Preflight/lock failures can leave a prior file untouched; its flag alone is not freshness proof. Native exit0, rounded stdout, null/stale JSON or a retained prior file is not success.

## Example request

Generate a Witte Modbus Poll setup from this map.

## Safety boundary

This skill does not write registers, force coils, broadcast, scan a network, or start unbounded polling. Unresolved engineering fields stay visible.

[View Build Modpoll (BETA) source on GitHub](https://github.com/studioxvii/modbus-skills/blob/main/plugins/modbus-skills/skills/build-modpoll/SKILL.md)
