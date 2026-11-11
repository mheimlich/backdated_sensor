# Backdated Sensor

A Home Assistant helper that mirrors a sensor or binary sensor and records each value at the
time it was captured, for example when a car measured its battery level, instead of when
Home Assistant received it. The capture time can come from an attribute, another entity, a
template or a fixed delay.

It needs Home Assistant 2026.10 or later.

## Installation

[![Open your Home Assistant instance and open this repository in the Marketplace.](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=mheimlich&repository=backdated_sensor&category=integration)

- **Marketplace** (Home Assistant 2026.11 or later) **or HACS:** open the repository with
  the button above, or add `https://github.com/mheimlich/backdated_sensor` as a custom
  repository of the type Integration. Download it, then restart Home Assistant.
- **By hand:** copy `custom_components/backdated_sensor` into the `custom_components`
  folder of your configuration, then restart Home Assistant.

## Setup in the UI

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=backdated_sensor)

Use the button above, or under **Settings → Devices & services → Helpers** choose **Create
helper → Backdated Sensor**:

1. Enter the name and choose the source, a sensor or a binary sensor.
2. Choose where the capture time comes from (see below) and fill in what that choice
   asks for. An attribute is chosen from a list of the entity's attributes, and a template
   is rendered once and refused if it fails or gives no time.
3. For a sensor, the unit of measurement, device class and state class are taken from the
   source and can be changed; for a binary sensor, the device class. Settings that do not
   fit together or cannot show the source's current state, such as a unit for a text, are
   refused.

The helper joins the source's device and is named like the device's other entities.
**Configure** on the helper changes everything but the name; another source has to be of
the same kind, sensor or binary sensor. The helper is then reloaded and keeps its value.
After a change, the current value stays until a value captured later arrives. When the
source's entity ID changes, the helper follows it.

## Values

A binary sensor source makes a binary sensor, which takes `on` and `off`. A sensor source
makes a sensor, which takes

- a number if it has a unit, a state class or a numeric device class,
- a time with the device class Timestamp or Uptime, and a date with Date,
- any text otherwise. With the device class Enumeration, the source's list of options
  comes along with each value.

States are shown as the source reports them, such as `disconnected`; the names the
source's integration translates them to do not carry over. A binary sensor's device class
names `on` and `off` as usual, such as Locked and Unlocked.

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
as separate state changes. The entity then waits until the source and those entities have
stayed unchanged for 5 seconds and pairs the source's value with the time it reads at
that moment, whichever arrived first. Reports less than 5 seconds apart therefore make one
value. A template counts as reading the entities it read when last rendered. Without other
entities, each report is taken at once. A template is rendered only when the source or an
entity it reads changes, never because the clock moved on.

## Setup in YAML

Add one entry per sensor under `sensor:` and per binary sensor under `binary_sensor:`:

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

binary_sensor:
  - platform: backdated_sensor
    name: Car locked captured
    source: binary_sensor.car_locked
    timestamp_attribute: data_captured_at
    device_class: lock
```

| Option | Required | Description |
| --- | --- | --- |
| `name` | yes | Name of the entity. |
| `source` | yes | Entity whose state is mirrored. |
| `timestamp_…` | yes | One combination from the table above. `timestamp_delay` takes a duration such as `"00:15:00"`. |
| `unique_id` | no | Makes the entity editable in the UI. |
| `unit_of_measurement`, `device_class`, `state_class` | no | As for any sensor; a binary sensor takes `device_class` only. They are not taken from the source. |

An entity set up in YAML belongs to no device.

## Entities

Each entity has the attribute `captured_at`, the capture time of its value.

### Behaviour

- **Which updates count.** A source update is taken only if its capture time is later than
  the one of the current value. Updates without a capture time, with an equal or earlier
  one, `unknown`, `unavailable` or a state the entity cannot take (see Values) are
  ignored, and the entity keeps its value.
- **Recorded time.** The value is written with its capture time as `last_updated`, so the
  history shows it from that moment. A capture time in the future is recorded as the
  current time. A value is never recorded before the latest row of the entity, since the
  history reads rows in time order; it then follows that row by a millisecond. Before its
  first value the entity is `unknown` from the time it was set up, so the first value after
  installation may be recorded at that time instead.
- **Restarts and reloads.** The entity restores its value and capture time and records it
  just after its last row, which after a reload is the `unavailable` row Home Assistant
  writes meanwhile. A newer value the source holds by then is taken at once.
- **Long-term statistics.** For a sensor with a `state_class`, the recorder compiles
  statistics every five minutes from the rows recorded so far, so periods compiled before a
  backdated value arrived hold the previous value. Once the hour in which the value arrived
  has been compiled, the sensor recomputes the affected hourly statistics from the recorded
  history (time-weighted mean, minimum and maximum) and rewrites them. It only rewrites
  hours that already exist. The five-minute statistics keep the previous value. A pending
  correction is kept across restarts.

## Tests

```sh
pip install -r requirements_test.txt
ruff check . && ruff format --check .
python -m pytest
```

GitHub Actions runs the same on every push and pull request, and checks the integration
with Home Assistant's hassfest.

## Icon

`assets/icon.svg` is the source of the icons in `custom_components/backdated_sensor/brand`,
which Home Assistant shows for the integration. After changing it, render them again:

```sh
cd custom_components/backdated_sensor/brand
magick -background none -density 96 ../../../assets/icon.svg -strip -depth 8 -define png:color-type=6 icon@2x.png
magick -background none -density 48 ../../../assets/icon.svg -strip -depth 8 -define png:color-type=6 icon.png
```

## License

MIT, see [LICENSE](LICENSE).
