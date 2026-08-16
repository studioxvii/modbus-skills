# Review Map

Review a raw or messy Modbus register map through parsing, normalization, linting, and grouped evidence exceptions when source review itself is the requested outcome.

## Use this when

The user wants end-to-end source-map review rather than an organized user-map compile.

## What you get back

- `map-draft.json` - Start here if you need the normalized draft map.
- `review.json` - Open this for the compact evidence status and grouped exceptions.
- `lint.json` - Detailed deterministic validation findings; normally use it only to investigate a problem.

## Example request

Clean and validate this complete Modbus register map.

## Safety boundary

This skill does not write registers, force coils, broadcast, scan a network, or start unbounded polling. Unresolved engineering fields stay visible.

[View Review Map source on GitHub](https://github.com/studioxvii/modbus-skills/blob/main/plugins/modbus-skills/skills/review-map/SKILL.md)
