# Evidence Status

- `confirmed`: A primary source or explicit human decision supports the value.
- `inferred`: The value was derived and still needs confirmation.
- `unresolved`: Available evidence does not determine the value.
- `rejected`: The value is invalid, unsafe, or unusable. Preserve its reason and source.

Match method is separate from status. Supported methods include `exact`, `coordinate-derived`, `ocr-derived`, `fuzzy`, and `inferred`.

An exact match can remain unresolved when its engineering meaning is unclear. OCR or
coordinate-derived evidence may need scoped source confirmation, but the confirmation
applies to the complete bounded source hash and page range unless specific exceptions
are listed. Never require a separate decision for each page or row. `blocked` is a
workflow state caused by one or more grouped unresolved decisions or explicit holds;
`ready` means automated checks found no decision requiring human input.
