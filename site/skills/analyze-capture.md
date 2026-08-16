# Analyze Capture

Analyze bounded Modbus samples and report communication, timing, signal, and raw-word evidence.

## Use this when

The user has capture data, JSON/CSV samples, or asks about stale, missing, flatline, or signal-quality problems.

## What you get back

- `analysis.json` - Open this result. It summarizes communication, missing planned reads, timing, signal, and raw-word findings for the supplied sample window.

## Example request

Analyze this Modbus capture for stale and missing samples.

## Safety boundary

This skill does not write registers, force coils, broadcast, scan a network, or start unbounded polling. Unresolved engineering fields stay visible.

[View Analyze Capture source on GitHub](https://github.com/studioxvii/modbus-skills/blob/main/plugins/modbus-skills/skills/analyze-capture/SKILL.md)
