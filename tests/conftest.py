"""Fixtures of the Backdated Sensor tests."""

from datetime import datetime
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

# The plugin's test configuration has a custom_components package of its own, which
# Home Assistant's loader would find unless this repository's is imported first.
import custom_components.backdated_sensor  # noqa: F401

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


async def setup_sensor(hass: HomeAssistant, **capture_time: Any) -> None:
    """Set up the backdated sensor of CONFIG, with other capture time options if given."""
    config = dict(CONFIG["sensor"][0])
    if capture_time:
        del config["timestamp_attribute"]
        config.update(capture_time)
    assert await async_setup_component(hass, "sensor", {"sensor": [config]})
    await hass.async_block_till_done()


BINARY_SOURCE = "binary_sensor.car_locked"
BINARY_ENTITY = "binary_sensor.car_locked_captured"


def report_state(
    hass: HomeAssistant,
    entity_id: str,
    value: str,
    captured: datetime,
    **attributes: Any,
) -> None:
    """Set an entity's state with the capture time and further attributes."""
    hass.states.async_set(
        entity_id, value, {"data_captured_at": captured.isoformat(), **attributes}
    )


async def setup_platform(hass: HomeAssistant, domain: str, **config: Any) -> None:
    """Set up one backdated entity of the domain from YAML."""
    entry = {
        "platform": "backdated_sensor",
        "timestamp_attribute": "data_captured_at",
        **config,
    }
    assert await async_setup_component(hass, domain, {domain: [entry]})
    await hass.async_block_till_done()
