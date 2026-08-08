# Evidence Status

- `confirmed`: A primary source or explicit human decision supports the value.
- `inferred`: The value was derived and still needs confirmation.
- `unresolved`: Available evidence does not determine the value.
- `rejected`: The value is invalid, unsafe, or unusable. Preserve its reason and source.

Match method is separate from status. Supported methods include `exact`, `coordinate-derived`, `ocr-derived`, `fuzzy`, and `inferred`.

An exact match can remain unresolved when its engineering meaning is unclear. OCR evidence becomes confirmed only after a human reviews the source page. `blocked` is a workflow state caused by an unresolved required item or explicit hold.
