# Plan Reads

Compile validated Modbus points into deterministic, bounded read blocks for function codes 01 through 04.

## Use this when

A validated map is ready and the user needs a read plan before building Node-RED, Modpoll (BETA), ModScan (BETA), or a tool pack.

## What you get back

- `read-plan.json` - Use this machine-readable list of bounded Modbus requests when building a target tool. It also records which points each request returns and why blocks were split.

## Example request

Group these Modbus points into bounded reads.

## Safety boundary

This skill does not write registers, force coils, broadcast, scan a network, or start unbounded polling. Unresolved engineering fields stay visible.

[View Plan Reads source on GitHub](https://github.com/studioxvii/modbus-skills/blob/main/plugins/modbus-skills/skills/plan-reads/SKILL.md)
