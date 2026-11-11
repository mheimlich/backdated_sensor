"""Tests of the ways to give the capture time."""

from datetime import UTC, datetime, timedelta
from typing import Any

import probatio
import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.recorder import Recorder
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.backdated_sensor.capture_time import kind_of, parse_time

from .conftest import CONFIG, ENTITY, SOURCE, report, setup_sensor

NOW = datetime(2026, 11, 10, 12, 20, tzinfo=UTC)
TIME_ENTITY = "sensor.car_reported"


@pytest.fixture(autouse=True)
def frozen_now(freezer: FrozenDateTimeFactory) -> FrozenDateTimeFactory:
    """Start every test at NOW."""
    freezer.move_to(NOW)
    return freezer


def _sensor(hass: HomeAssistant) -> tuple[str, datetime]:
    """Return the sensor's state and its update time."""
    state = hass.states.get(ENTITY)
    return state.state, state.last_updated


async def _set(hass: HomeAssistant, entity_id: str, state: str, **attributes) -> None:
    hass.states.async_set(entity_id, state, attributes)
    await hass.async_block_till_done()


async def _wait(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: int
) -> None:
    freezer.tick(seconds)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_a_time_from_another_entity_is_paired_once_both_settled(
    recorder_mock: Recorder, hass: HomeAssistant, frozen_now: FrozenDateTimeFactory
) -> None:
    """A time arriving before its value is not paired with the previous value."""
    await _set(hass, SOURCE, "30")
    await _set(hass, TIME_ENTITY, (NOW - timedelta(hours=6)).isoformat())
    await setup_sensor(hass, timestamp_entity=TIME_ENTITY)
    assert _sensor(hass) == ("30", NOW - timedelta(hours=6))

    await _set(hass, TIME_ENTITY, (NOW - timedelta(hours=1)).isoformat())
    await _set(hass, SOURCE, "50")
    await _wait(hass, frozen_now, 4)
    assert _sensor(hass) == ("30", NOW - timedelta(hours=6))

    await _wait(hass, frozen_now, 1)
    assert _sensor(hass) == ("50", NOW - timedelta(hours=1))


async def test_a_value_waits_for_a_later_time(
    recorder_mock: Recorder, hass: HomeAssistant, frozen_now: FrozenDateTimeFactory
) -> None:
    """A new value is taken only once the entity gives a later time."""
    await _set(hass, SOURCE, "30")
    await _set(hass, TIME_ENTITY, (NOW - timedelta(hours=6)).isoformat())
    await setup_sensor(hass, timestamp_entity=TIME_ENTITY)

    await _set(hass, SOURCE, "50")
    await _wait(hass, frozen_now, 10)
    assert _sensor(hass) == ("30", NOW - timedelta(hours=6))

    await _set(hass, TIME_ENTITY, (NOW - timedelta(hours=1)).isoformat())
    await _wait(hass, frozen_now, 5)
    assert _sensor(hass) == ("50", NOW - timedelta(hours=1))


async def test_an_attribute_of_another_entity_in_unix_milliseconds(
    recorder_mock: Recorder, hass: HomeAssistant
) -> None:
    """The attribute's Unix time is read as milliseconds, as it is too large for seconds."""
    captured = NOW - timedelta(hours=2)
    await _set(hass, SOURCE, "30")
    await _set(hass, TIME_ENTITY, "on", at=int(captured.timestamp() * 1000))
    await setup_sensor(hass, timestamp_entity=TIME_ENTITY, timestamp_attribute="at")

    assert _sensor(hass) == ("30", captured)


async def test_the_last_change_of_another_entity(
    recorder_mock: Recorder, hass: HomeAssistant, frozen_now: FrozenDateTimeFactory
) -> None:
    """The time is the entity's last_changed, which a new attribute does not move."""
    frozen_now.move_to(NOW - timedelta(hours=2))
    await _set(hass, "binary_sensor.car_online", "on")
    frozen_now.move_to(NOW)
    await _set(hass, "binary_sensor.car_online", "on", reason="poll")
    await _set(hass, SOURCE, "30")
    await setup_sensor(
        hass,
        timestamp_entity="binary_sensor.car_online",
        timestamp_property="last_changed",
    )

    assert _sensor(hass) == ("30", NOW - timedelta(hours=2))


async def test_a_template_reading_only_the_source_takes_each_report_at_once(
    recorder_mock: Recorder, hass: HomeAssistant
) -> None:
    """Without other entities, nothing waits, so quick reports are all kept."""
    report(hass, "30", NOW - timedelta(hours=6))
    await setup_sensor(
        hass, timestamp_template="{{ state_attr(source, 'data_captured_at') }}"
    )

    report(hass, "40", NOW - timedelta(hours=5))
    assert _sensor(hass) == ("40", NOW - timedelta(hours=5))
    report(hass, "50", NOW - timedelta(hours=4))
    assert _sensor(hass) == ("50", NOW - timedelta(hours=4))


async def test_a_template_reading_another_entity_waits(
    recorder_mock: Recorder, hass: HomeAssistant, frozen_now: FrozenDateTimeFactory
) -> None:
    """The template's other entity counts like a timestamp entity."""
    await _set(hass, SOURCE, "30")
    await _set(hass, TIME_ENTITY, (NOW - timedelta(hours=6)).isoformat())
    await setup_sensor(hass, timestamp_template="{{ states('sensor.car_reported') }}")

    await _set(hass, TIME_ENTITY, (NOW - timedelta(hours=1)).isoformat())
    await _set(hass, SOURCE, "50")
    assert _sensor(hass) == ("30", NOW - timedelta(hours=6))

    await _wait(hass, frozen_now, 5)
    assert _sensor(hass) == ("50", NOW - timedelta(hours=1))


async def test_now_in_a_template_is_the_reception_time(
    recorder_mock: Recorder, hass: HomeAssistant, frozen_now: FrozenDateTimeFactory
) -> None:
    """The clock moving on renders the template but records nothing."""
    await _set(hass, SOURCE, "30")
    await setup_sensor(hass, timestamp_template="{{ now() - timedelta(minutes=15) }}")
    assert _sensor(hass) == ("30", NOW - timedelta(minutes=15))

    await _wait(hass, frozen_now, 60)
    await _set(hass, SOURCE, "40")
    received = NOW + timedelta(minutes=1)
    assert _sensor(hass) == ("40", received - timedelta(minutes=15))

    await _wait(hass, frozen_now, 180)
    assert _sensor(hass) == ("40", received - timedelta(minutes=15))


async def test_a_delay_is_subtracted_from_the_reception_time(
    recorder_mock: Recorder, hass: HomeAssistant, frozen_now: FrozenDateTimeFactory
) -> None:
    """The time is when the source's state was received, less the delay."""
    frozen_now.move_to(NOW - timedelta(minutes=1))
    await _set(hass, SOURCE, "30")
    frozen_now.move_to(NOW)
    await setup_sensor(hass, timestamp_delay="00:15:00")
    assert _sensor(hass) == ("30", NOW - timedelta(minutes=16))

    await _wait(hass, frozen_now, 60)
    await _set(hass, SOURCE, "40")
    assert _sensor(hass) == ("40", NOW + timedelta(minutes=1) - timedelta(minutes=15))


@pytest.mark.parametrize(
    "options",
    [
        {},
        {"timestamp_attribute": "a", "timestamp_template": "{{ now() }}"},
        {"timestamp_property": "last_changed"},
        {
            "timestamp_entity": "sensor.a",
            "timestamp_attribute": "a",
            "timestamp_property": "last_changed",
        },
    ],
)
def test_options_giving_no_or_several_times_are_refused(
    options: dict[str, Any],
) -> None:
    """Exactly one way to give the time is accepted."""
    with pytest.raises(probatio.Invalid):
        kind_of(options)


async def test_yaml_giving_two_times_sets_up_nothing(
    recorder_mock: Recorder, hass: HomeAssistant
) -> None:
    """The YAML entry is refused as a whole."""
    config = {**CONFIG["sensor"][0], "timestamp_delay": "00:15:00"}
    assert await async_setup_component(hass, "sensor", {"sensor": [config]})
    await hass.async_block_till_done()

    assert hass.states.get(ENTITY) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (NOW, NOW),
        ("2026-11-10T13:20:00+01:00", NOW),
        ("2026-11-10 12:20:00Z", NOW),
        (" 2026-11-10T12:20:00+00:00 ", NOW),
        (NOW.timestamp(), NOW),
        (str(int(NOW.timestamp())), NOW),
        (int(NOW.timestamp() * 1000), NOW),
        ("abc", None),
        ("", None),
        (None, None),
        (True, None),
        (0, None),
        (float("nan"), None),
    ],
)
def test_parse_time(value: Any, expected: datetime | None) -> None:
    """Datetimes, ISO 8601 texts and Unix times in seconds or milliseconds are read."""
    assert parse_time(value) == expected


def test_parse_time_reads_a_time_without_offset_as_local_time() -> None:
    """A naive time is in Home Assistant's time zone."""
    zone = dt_util.get_default_time_zone()
    local = datetime(2026, 11, 10, 13, 20, tzinfo=zone)
    assert parse_time("2026-11-10T13:20:00") == local.astimezone(UTC)
