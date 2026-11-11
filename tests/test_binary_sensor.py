"""Tests of the backdated binary sensor."""

from datetime import UTC, datetime, timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.recorder import Recorder
from homeassistant.core import HomeAssistant, State
from pytest_homeassistant_custom_component.common import (
    async_mock_restore_state_shutdown_restart,
    mock_restore_cache_with_extra_data,
)

from .conftest import BINARY_ENTITY, BINARY_SOURCE, report_state, setup_platform

NOW = datetime(2026, 11, 10, 12, 20, tzinfo=UTC)


@pytest.fixture(autouse=True)
def frozen_now(freezer: FrozenDateTimeFactory) -> FrozenDateTimeFactory:
    """Start every test at NOW."""
    freezer.move_to(NOW)
    return freezer


async def _setup(hass: HomeAssistant) -> None:
    await setup_platform(
        hass,
        "binary_sensor",
        name="Car locked captured",
        source=BINARY_SOURCE,
        device_class="lock",
    )


async def test_states_are_recorded_at_their_capture_time(
    recorder_mock: Recorder, hass: HomeAssistant
) -> None:
    """On and off are mirrored; other states are ignored."""
    report_state(hass, BINARY_SOURCE, "on", NOW - timedelta(hours=6))
    await _setup(hass)

    state = hass.states.get(BINARY_ENTITY)
    assert state.state == "on"
    assert state.last_updated == NOW - timedelta(hours=6)
    assert state.attributes["device_class"] == "lock"

    report_state(hass, BINARY_SOURCE, "off", NOW - timedelta(hours=3))
    await hass.async_block_till_done()
    report_state(hass, BINARY_SOURCE, "jammed", NOW - timedelta(hours=2))
    await hass.async_block_till_done()

    state = hass.states.get(BINARY_ENTITY)
    assert state.state == "off"
    assert state.last_updated == NOW - timedelta(hours=3)


async def test_the_state_is_restored(
    recorder_mock: Recorder, hass: HomeAssistant
) -> None:
    """The restore data holds the state as a bool."""
    captured = NOW - timedelta(hours=5)
    mock_restore_cache_with_extra_data(
        hass,
        (
            (
                State(BINARY_ENTITY, "off"),
                {
                    "native_value": False,
                    "captured": captured.isoformat(),
                    "written": captured.timestamp(),
                    "correct_statistics_from": None,
                },
            ),
        ),
    )
    await _setup(hass)

    state = hass.states.get(BINARY_ENTITY)
    assert state.state == "off"
    assert state.attributes["captured_at"] == captured.isoformat()

    data = await async_mock_restore_state_shutdown_restart(hass)
    assert data.last_states[BINARY_ENTITY].extra_data.as_dict()["native_value"] is False
