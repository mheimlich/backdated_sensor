"""Tests of the state and the recorded history of the backdated sensor."""

from datetime import UTC, datetime, timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.recorder import Recorder
from homeassistant.components.recorder.history import get_significant_states
from homeassistant.core import HomeAssistant, State
from pytest_homeassistant_custom_component.common import (
    async_mock_restore_state_shutdown_restart,
    mock_restore_cache_with_extra_data,
)
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from .conftest import ENTITY, report, report_state, setup_platform, setup_sensor

TEXT_SOURCE = "sensor.car_charging"

NOW = datetime(2026, 11, 10, 12, 20, tzinfo=UTC)


async def _history(hass: HomeAssistant) -> list[tuple[str, datetime]]:
    """Return every recorded row of the sensor as (state, last_updated)."""
    await async_wait_recording_done(hass)
    rows = await hass.async_add_executor_job(
        lambda: get_significant_states(
            hass,
            NOW - timedelta(days=2),
            None,
            [ENTITY],
            include_start_time_state=False,
            significant_changes_only=False,
        )
    )
    return [(state.state, state.last_updated) for state in rows.get(ENTITY, [])]


@pytest.fixture(autouse=True)
def frozen_now(freezer: FrozenDateTimeFactory) -> FrozenDateTimeFactory:
    """Start every test at NOW."""
    freezer.move_to(NOW)
    return freezer


async def test_values_are_recorded_at_their_capture_time(
    recorder_mock: Recorder, hass: HomeAssistant, frozen_now: FrozenDateTimeFactory
) -> None:
    """The first value comes from the source at setup, later ones from its updates."""
    report(hass, "30", NOW - timedelta(hours=6))
    await setup_sensor(hass)

    state = hass.states.get(ENTITY)
    assert state.state == "30"
    assert state.last_updated == NOW - timedelta(hours=6)
    assert state.attributes["captured_at"] == "2026-11-10T06:20:00+00:00"
    assert state.attributes["unit_of_measurement"] == "%"
    assert state.attributes["state_class"] == "measurement"

    frozen_now.tick(60)
    report(hass, "50", NOW - timedelta(hours=3))
    await hass.async_block_till_done()

    state = hass.states.get(ENTITY)
    assert state.state == "50"
    assert state.last_updated == NOW - timedelta(hours=3)
    assert await _history(hass) == [
        ("30", NOW - timedelta(hours=6)),
        ("50", NOW - timedelta(hours=3)),
    ]


async def test_an_unchanged_value_keeps_its_last_changed(
    recorder_mock: Recorder, hass: HomeAssistant
) -> None:
    """A new report of the same value updates the capture time only."""
    report(hass, "30", NOW - timedelta(hours=6))
    await setup_sensor(hass)
    report(hass, "30", NOW - timedelta(hours=2))
    await hass.async_block_till_done()

    state = hass.states.get(ENTITY)
    assert state.last_changed == NOW - timedelta(hours=6)
    assert state.last_updated == NOW - timedelta(hours=2)
    assert state.attributes["captured_at"] == "2026-11-10T10:20:00+00:00"


async def test_updates_without_a_later_capture_time_are_ignored(
    recorder_mock: Recorder, hass: HomeAssistant
) -> None:
    """Missing, repeated or older capture times and unavailable states change nothing."""
    report(hass, "30", NOW - timedelta(hours=6))
    await setup_sensor(hass)

    report(hass, "31", None)
    await hass.async_block_till_done()
    report(hass, "32", NOW - timedelta(hours=6))
    await hass.async_block_till_done()
    report(hass, "33", NOW - timedelta(hours=7))
    await hass.async_block_till_done()
    hass.states.async_set("sensor.car_battery", "unavailable", {})
    await hass.async_block_till_done()
    report(hass, "abc", NOW - timedelta(hours=1))
    await hass.async_block_till_done()

    state = hass.states.get(ENTITY)
    assert state.state == "30"
    assert state.last_updated == NOW - timedelta(hours=6)
    assert await _history(hass) == [("30", NOW - timedelta(hours=6))]


async def test_a_capture_time_in_the_future_is_recorded_as_now(
    recorder_mock: Recorder, hass: HomeAssistant
) -> None:
    """The sensor never records a state ahead of the current time."""
    report(hass, "30", NOW + timedelta(hours=1))
    await setup_sensor(hass)

    state = hass.states.get(ENTITY)
    assert state.last_updated == NOW
    assert state.attributes["captured_at"] == "2026-11-10T13:20:00+00:00"


async def test_a_value_captured_before_the_latest_row_follows_it(
    recorder_mock: Recorder, hass: HomeAssistant, frozen_now: FrozenDateTimeFactory
) -> None:
    """A row written by Home Assistant itself is not overtaken by an earlier capture time."""
    report(hass, "30", NOW - timedelta(hours=6))
    await setup_sensor(hass)
    hass.states.async_set(ENTITY, "unavailable", {})
    await hass.async_block_till_done()

    frozen_now.tick(60)
    report(hass, "50", NOW - timedelta(hours=3))
    await hass.async_block_till_done()

    state = hass.states.get(ENTITY)
    assert state.state == "50"
    assert state.last_updated_timestamp == pytest.approx(NOW.timestamp() + 0.001)
    assert [value for value, _ in await _history(hass)] == ["30", "unavailable", "50"]


async def test_a_restart_continues_after_the_last_row(
    recorder_mock: Recorder, hass: HomeAssistant
) -> None:
    """The restored value is written just after the last row and keeps its capture time."""
    captured = NOW - timedelta(hours=5)
    written = NOW - timedelta(hours=4)
    mock_restore_cache_with_extra_data(
        hass,
        (
            (
                State(ENTITY, "40"),
                {
                    "native_value": 40,
                    "captured": captured.isoformat(),
                    "written": written.timestamp(),
                    "correct_statistics_from": None,
                },
            ),
        ),
    )
    await setup_sensor(hass)

    state = hass.states.get(ENTITY)
    assert state.state == "40"
    assert state.attributes["captured_at"] == captured.isoformat()
    assert state.last_updated_timestamp == pytest.approx(written.timestamp() + 0.001)

    report(hass, "45", NOW - timedelta(hours=6))
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == "40"

    report(hass, "45", NOW - timedelta(hours=2))
    await hass.async_block_till_done()
    state = hass.states.get(ENTITY)
    assert state.state == "45"
    assert state.last_updated == NOW - timedelta(hours=2)


async def test_a_newer_source_value_replaces_the_restored_one(
    recorder_mock: Recorder, hass: HomeAssistant
) -> None:
    """A report the source received while Home Assistant was down is taken at setup."""
    mock_restore_cache_with_extra_data(
        hass,
        (
            (
                State(ENTITY, "40"),
                {
                    "native_value": 40,
                    "captured": (NOW - timedelta(hours=5)).isoformat(),
                    "written": (NOW - timedelta(hours=5)).timestamp(),
                    "correct_statistics_from": None,
                },
            ),
        ),
    )
    report(hass, "55", NOW - timedelta(hours=1))
    await setup_sensor(hass)

    state = hass.states.get(ENTITY)
    assert state.state == "55"
    assert state.last_updated == NOW - timedelta(hours=1)


async def test_a_text_is_mirrored_without_unit_or_classes(
    recorder_mock: Recorder, hass: HomeAssistant
) -> None:
    """A sensor without unit, device class or state class takes any text."""
    report_state(hass, TEXT_SOURCE, "charging", NOW - timedelta(hours=2))
    await setup_platform(
        hass, "sensor", name="Car charging captured", source=TEXT_SOURCE
    )

    state = hass.states.get("sensor.car_charging_captured")
    assert state.state == "charging"
    assert state.last_updated == NOW - timedelta(hours=2)


async def test_an_enum_takes_the_options_of_the_source_and_keeps_them(
    recorder_mock: Recorder, hass: HomeAssistant
) -> None:
    """The options come with each value and are stored for a restart."""
    options = ["connected", "disconnected"]
    report_state(
        hass, TEXT_SOURCE, "connected", NOW - timedelta(hours=2), options=options
    )
    await setup_platform(
        hass,
        "sensor",
        name="Car charging captured",
        source=TEXT_SOURCE,
        device_class="enum",
    )

    state = hass.states.get("sensor.car_charging_captured")
    assert state.state == "connected"
    assert state.attributes["options"] == options

    data = await async_mock_restore_state_shutdown_restart(hass)
    stored = data.last_states["sensor.car_charging_captured"].extra_data.as_dict()
    assert stored["native_value"] == "connected"
    assert stored["options"] == options


async def test_a_timestamp_is_mirrored_as_a_time(
    recorder_mock: Recorder, hass: HomeAssistant
) -> None:
    """A timestamp sensor reads the source's state as a time."""
    report_state(
        hass, TEXT_SOURCE, "2026-11-09T21:16:19+00:00", NOW - timedelta(hours=2)
    )
    await setup_platform(
        hass,
        "sensor",
        name="Car charging captured",
        source=TEXT_SOURCE,
        device_class="timestamp",
    )

    assert hass.states.get("sensor.car_charging_captured").state == (
        "2026-11-09T21:16:19+00:00"
    )
