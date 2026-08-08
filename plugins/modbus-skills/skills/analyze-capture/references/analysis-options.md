# Capture Analysis Options

JSON input can contain point metadata and samples. CSV input is a flat sample table. Use columns such as `sample_id`, `point_id`, `timestamp`, `value`, `response_ms`, `error`, `success`, and `raw_words`. Put multiple raw words in a JSON array or separate them with spaces. Supply thresholds through the options file when CSV has no point metadata.

- `now`: Use an explicit ISO-8601 time. Otherwise, use the latest sample and report this assumption.
- `max_samples`: The default and hard maximum are 100000.
- `expected_interval_seconds`: Use a positive scalar or point-ID object to detect sample gaps in flat CSV captures.
- `stale_after_seconds`: Use a nonnegative scalar or point-ID object. There is no default.
- `flatline_min_samples`: The default is 3. The minimum is 2.
- `ranges`: Use a point-ID object with `minimum` and `maximum`, or `min` and `max`. There are no defaults.
- `rate_limits`: Use point-ID scalar values or objects with `rate_of_change_limit` or `maximum_rate_per_second`. There is no default.
- `counter_specs`: Use a point-ID object with `modulus` and optional wrap fractions. The default high and low wrap fractions are 0.9 and 0.1.

Do not invent engineering thresholds. Omit checks without supported thresholds and report the limitation.
