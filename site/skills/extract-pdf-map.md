# Extract PDF Map

Extract traceable Modbus register candidates, source coverage, and page evidence from a PDF manual or bounded page range.

## Use this when

The source is a PDF register manual and the user wants extraction evidence before normalization or compilation.

## What you get back

- `pdf-extraction.json` - Open this only when reviewing extraction. It contains the candidate rows, source locations, automated checks, rejected rows, and grouped exceptions. It does not contain page images or full OCR text.

## Example request

Extract register tables with page evidence from this PDF.

## Safety boundary

This skill does not write registers, force coils, broadcast, scan a network, or start unbounded polling. Unresolved engineering fields stay visible.

[View Extract PDF Map source on GitHub](https://github.com/studioxvii/modbus-skills/blob/main/plugins/modbus-skills/skills/extract-pdf-map/SKILL.md)
