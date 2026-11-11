# Backdated Sensor

A Home Assistant sensor that mirrors a numeric sensor and records each value at the time it
was captured, for example when a car measured its battery level, instead of when Home
Assistant received it. The capture time can come from an attribute, another entity, a
template or a fixed delay.

## Setup in the UI

Under **Settings → Devices & services → Helpers**, choose **Create helper → Backdated
Sensor**:

1. Enter the name and choose the source sensor.
2. Choose where the capture time comes from (see below) and fill in what that choice
   asks for. An attribute is chosen from a list of the entity's attributes, and a template
   is rendered once and refused if it fails or gives no time.
3. The unit of measurement, device class and state class are taken from the source and
   can be changed. A unit or state class that does not fit the device class is refused.

The sensor joins the source's device and is named like the device's other entities.
**Configure** on the helper changes everything but the name; the sensor is then reloaded
and keeps its value. After a change, the current value stays until a value captured later
arrives. When the source's entity ID changes, the sensor follows it.

## Capture time

| Choice in the UI | YAML options | Capture time |
| --- | --- | --- |
| Attribute of the source sensor | `timestamp_attribute` | The attribute's time. |
| State of another entity | `timestamp_entity` | The entity's state, such as that of a timestamp sensor. |
| Attribute of another entity | `timestamp_entity`, `timestamp_attribute` | The attribute's time. |
| Last change of another entity | `timestamp_entity`, `timestamp_property` | The entity's `last_changed`, `last_updated` or `last_reported`. |
| Template | `timestamp_template` | What the template renders. The variable `source` holds the source's entity ID, and `now()` gives the time a change was received. |
| Time received, less a delay | `timestamp_delay` | When the source's state was received (its `last_updated`), less the delay. |

A time is read from a datetime, an ISO 8601 text (local time if it has no offset) or a
Unix time in seconds, or in milliseconds if the number is 10<sup>11</sup> or more.

When the time comes from entities other than the source, the value and the time arrive
as separate state changes. The sensor then waits until the source and those entities have
stayed unchanged for 5 seconds and pairs the source's value with the time it reads at
that moment, whichever arrived first. Reports less than 5 seconds apart therefore make one
value. A template counts as reading the entities it read when last rendered. Without other
entities, each report is taken at once. A template is rendered only when the source or an
entity it reads changes, never because the clock moved on.

## Setup in YAML

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

  - platform: backdated_sensor
    name: Car outside temperature captured
    source: sensor.car_outside_temperature
    timestamp_entity: sensor.car_last_reported
    unit_of_measurement: °C
    device_class: temperature
    state_class: measurement
```

| Option | Required | Description |
| --- | --- | --- |
| `name` | yes | Name of the sensor. |
| `source` | yes | Sensor whose numeric state is mirrored. |
| `timestamp_…` | yes | One combination from the table above. `timestamp_delay` takes a duration such as `"00:15:00"`. |
| `unique_id` | no | Makes the sensor editable in the UI. |
| `unit_of_measurement`, `device_class`, `state_class` | no | As for any sensor. They are not taken from the source. |

A sensor set up in YAML belongs to no device.

## Sensor

The sensor has the attribute `captured_at`, the capture time of its value.

### Behaviour

- **Which updates count.** A source update is taken only if its capture time is later than
  the one of the current value. Updates without a capture time, with an equal or earlier
  one, `unknown`, `unavailable` or a non-numeric state are ignored, and the sensor keeps
  its value.
- **Recorded time.** The value is written with its capture time as `last_updated`, so the
  history shows it from that moment. A capture time in the future is recorded as the
  current time. A value is never recorded before the latest row of the sensor, since the
  history reads rows in time order; it then follows that row by a millisecond. Before its
  first value the sensor is `unknown` from the time it was set up, so the first value after
  installation may be recorded at that time instead.
- **Restarts and reloads.** The sensor restores its value and capture time and records it
  just after its last row, which after a reload is the `unavailable` row Home Assistant
  writes meanwhile. A newer value the source holds by then is taken at once.
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
