# Probe Request

Use one or more of these targets:

- `node-red`
- `modpoll`
- `modscan`

Use this minimum request shape:

```json
{
  "targets": ["node-red"],
  "max_gap": 0,
  "points": [
    {
      "logical_point_id": "raw_probe",
      "route_id": "device-route",
      "unit_id": 1,
      "area": "holding-register",
      "protocol_offset": 0,
      "datatype": "unknown",
      "word_count": 2
    }
  ]
}
```

## Safe limits

- Default to one contiguous point, one target, and `max_gap` 0.
- Treat the generated files as a disabled or operator-controlled probe plan.
- At the external gate, issue one physical read and stop.
- For Witte desktop automation, use an interval of at least 1000 ms and no more than five aggregate requests per second on one route.
- Stop on repeated timeouts, exception responses, or an unexpected unit response.
- The generator does not claim that a target application ran or stopped the probe.

Do not put credentials or private endpoint values in the request artifact.
