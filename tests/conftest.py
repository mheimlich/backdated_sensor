"""Fixtures of the Backdated Sensor tests."""

from datetime import datetime

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

pytest_plugins = "pytest_homeassistant_custom_component"

SOURCE = "sensor.car_battery"
ENTITY = "sensor.car_battery_captured"
CONFIG = {
    "sensor": [
        {
            "platform": "backdated_sensor",
            "name": "Car battery captured",
            "unique_id": "car_battery_captured",
            "source": SOURCE,
            "timestamp_attribute": "data_captured_at",
            "unit_of_measurement": "%",
            "device_class": "battery",
            "state_class": "measurement",
        }
    ]
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load the integration from custom_components in every test."""
    yield


@pytest.fixture
async def mock_recorder_before_hass(async_setup_recorder_instance) -> None:
    """Set up the recorder before Home Assistant, for recorder_mock."""


def report(hass: HomeAssistant, value: str, captured: datetime | None) -> None:
    """Set the source state, with the capture time if one is given."""
    attributes = {
        "unit_of_measurement": "%",
        "device_class": "battery",
        "state_class": "measurement",
    }
    if captured is not None:
        attributes["data_captured_at"] = captured.isoformat()
    hass.states.async_set(SOURCE, value, attributes)


async def setup_sensor(hass: HomeAssistant) -> None:
    """Set up the backdated sensor of CONFIG."""
    assert await async_setup_component(hass, "sensor", CONFIG)
    await hass.async_block_till_done()
