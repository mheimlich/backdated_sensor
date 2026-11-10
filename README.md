# Backdated Sensor

A Home Assistant sensor that mirrors a numeric sensor and records each value at the time one
of the source's attributes gives, for example when a car captured its battery level, instead
of when Home Assistant received it.

## Configuration

Add one entry per sensor under `sensor:`:

```yaml
sensor:
  - platform: backdated_sensor
    name: Car battery captured
    unique_id: car_battery_captured
    source: sensor.car_battery_level
    timestamp_attribute: data_captured_at
    unit_of_measurement: "%"
    device_class: battery
    state_class: measurement
```

| Option | Required | Description |
| --- | --- | --- |
| `name` | yes | Name of the sensor. |
| `source` | yes | Sensor whose numeric state is mirrored. |
| `timestamp_attribute` | yes | Attribute of the source holding the capture time, as an ISO 8601 string or a datetime. |
| `unique_id` | no | Makes the sensor editable in the UI. |
| `unit_of_measurement`, `device_class`, `state_class` | no | As for any sensor. They are not taken from the source. |

The sensor has the attribute `captured_at`, the capture time of its value.

## Behaviour

- **Which updates count.** A source update is taken only if its capture time is later than
  the one of the current value. Updates without the attribute, with an equal or earlier
  time, `unknown`, `unavailable` or a non-numeric state are ignored, and the sensor keeps
  its value.
- **Recorded time.** The value is written with its capture time as `last_updated`, so the
  history shows it from that moment. A capture time in the future is recorded as the
  current time. A value is never recorded before the latest row of the sensor, since the
  history reads rows in time order; it then follows that row by a millisecond. Before its
  first value the sensor is `unknown` from the time it was set up, so the first value after
  installation may be recorded at that time instead.
- **Restarts.** After a restart the sensor restores its value and capture time and records
  it just after its last row. A newer value the source holds by then is taken at once.
- **Long-term statistics.** With a `state_class`, the recorder compiles statistics every
  five minutes from the rows recorded so far, so periods compiled before a backdated value
  arrived hold the previous value. Once the hour in which the value arrived has been
  compiled, the sensor recomputes the affected hourly statistics from the recorded history
  (time-weighted mean, minimum and maximum) and rewrites them. It only rewrites hours that
  already exist. The five-minute statistics keep the previous value. A pending correction
  is kept across restarts.

## Tests

```sh
python -m pytest
```

with `pytest-homeassistant-custom-component` installed (see `requirements_test.txt`).
