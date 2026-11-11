"""Tests of creating a backdated sensor in the UI and changing its options."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.recorder import Recorder
from homeassistant.components.recorder.history import get_significant_states
from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult, FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.backdated_sensor.const import DOMAIN

from .conftest import ENTITY, SOURCE, report

NOW = datetime(2026, 11, 10, 12, 20, tzinfo=UTC)
CAPTURED = NOW - timedelta(hours=6)

TIME_ENTITY = "sensor.car_reported"

DETAILS = {
    "unit_of_measurement": "%",
    "device_class": "battery",
    "state_class": "measurement",
}
OPTIONS = {
    "name": "Car battery captured",
    "source": SOURCE,
    "timestamp_attribute": "data_captured_at",
    **DETAILS,
}


@pytest.fixture(autouse=True)
def frozen_now(freezer: FrozenDateTimeFactory) -> FrozenDateTimeFactory:
    """Start every test at NOW."""
    freezer.move_to(NOW)
    return freezer


@pytest.fixture
def source_device(hass: HomeAssistant) -> dr.DeviceEntry:
    """Register the source on a device of another integration."""
    car = MockConfigEntry(domain="car")
    car.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=car.entry_id, identifiers={("car", "car")}, name="Car"
    )
    er.async_get(hass).async_get_or_create(
        "sensor",
        "car",
        "battery",
        suggested_object_id="car_battery",
        config_entry=car,
        device_id=device.id,
    )
    return device


async def _set_up_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Set up a sensor as the dialog would have created it."""
    entry = MockConfigEntry(domain=DOMAIN, title=OPTIONS["name"], options=OPTIONS)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _suggested(result: FlowResult) -> dict[str, Any]:
    """Return the values the form shows before any input."""
    return {
        str(key): key.description["suggested_value"]
        for key in result["data_schema"].schema
        if key.description and "suggested_value" in key.description
    }


def _selector(result: FlowResult, name: str) -> Any:
    """Return the selector of a form field."""
    return next(
        selector
        for key, selector in result["data_schema"].schema.items()
        if key == name
    )


async def _start(hass: HomeAssistant) -> FlowResult:
    """Run the first step of the setup dialog, which leads to the capture time menu."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": OPTIONS["name"], "source": SOURCE}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "timestamp"
    return result


async def _choose(hass: HomeAssistant, result: FlowResult, kind: str) -> FlowResult:
    """Choose a kind of capture time in the menu."""
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": kind}
    )


async def test_the_dialog_creates_a_sensor_on_the_source_device(
    recorder_mock: Recorder, hass: HomeAssistant, source_device: dr.DeviceEntry
) -> None:
    """The source's attributes are offered, and its unit and classes suggested."""
    report(hass, "30", CAPTURED)
    result = await _choose(hass, await _start(hass), "source_attribute")

    assert result["step_id"] == "source_attribute"
    assert _selector(result, "timestamp_attribute").config["entity_id"] == SOURCE
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"timestamp_attribute": "data_captured_at"}
    )

    assert result["step_id"] == "details"
    assert _suggested(result) == DETAILS
    result = await hass.config_entries.flow.async_configure(result["flow_id"], DETAILS)
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Car battery captured"
    entry = result["result"]
    assert entry.options == OPTIONS
    state = hass.states.get(ENTITY)
    assert state.state == "30"
    assert state.last_updated == CAPTURED
    assert state.attributes["device_class"] == "battery"
    registered = er.async_get(hass).async_get(ENTITY)
    assert registered.unique_id == entry.entry_id
    assert registered.config_entry_id == entry.entry_id
    assert registered.device_id == source_device.id


@pytest.mark.parametrize(
    ("kind", "inputs", "options"),
    [
        (
            "entity_state",
            [{"timestamp_entity": TIME_ENTITY}],
            {"timestamp_entity": TIME_ENTITY},
        ),
        (
            "entity_attribute",
            [{"timestamp_entity": TIME_ENTITY}, {"timestamp_attribute": "at"}],
            {"timestamp_entity": TIME_ENTITY, "timestamp_attribute": "at"},
        ),
        (
            "entity_change",
            [{"timestamp_entity": TIME_ENTITY, "timestamp_property": "last_updated"}],
            {"timestamp_entity": TIME_ENTITY, "timestamp_property": "last_updated"},
        ),
        (
            "template",
            [{"timestamp_template": "{{ states('sensor.car_reported') }}"}],
            {"timestamp_template": "{{ states('sensor.car_reported') }}"},
        ),
        (
            "delay",
            [{"timestamp_delay": {"hours": 0, "minutes": 15, "seconds": 0}}],
            {"timestamp_delay": {"hours": 0, "minutes": 15, "seconds": 0}},
        ),
    ],
)
async def test_each_kind_of_capture_time_creates_a_sensor(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    kind: str,
    inputs: list[dict[str, Any]],
    options: dict[str, Any],
) -> None:
    """The options hold what the kind's steps asked for, and the sensor is set up."""
    report(hass, "30", CAPTURED)
    hass.states.async_set(TIME_ENTITY, CAPTURED.isoformat(), {"at": "x"})
    result = await _choose(hass, await _start(hass), kind)
    for user_input in inputs:
        assert result["type"] is FlowResultType.FORM
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input
        )
    if kind == "entity_attribute":
        assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], DETAILS)
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].options == {
        "name": OPTIONS["name"],
        "source": SOURCE,
        **options,
        **DETAILS,
    }
    assert hass.states.get(ENTITY) is not None


async def test_the_attribute_of_another_entity_is_chosen_from_its_attributes(
    recorder_mock: Recorder, hass: HomeAssistant
) -> None:
    """The second step lists the attributes of the entity chosen in the first."""
    result = await _choose(hass, await _start(hass), "entity_attribute")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"timestamp_entity": TIME_ENTITY}
    )

    assert result["step_id"] == "entity_attribute_name"
    assert _selector(result, "timestamp_attribute").config["entity_id"] == TIME_ENTITY


@pytest.mark.parametrize(
    ("kind", "user_input", "error"),
    [
        ("entity_state", {"timestamp_entity": SOURCE}, "entity_is_source"),
        ("template", {"timestamp_template": "{{ 1 / 0 }}"}, "template_invalid"),
        ("template", {"timestamp_template": "soon"}, "template_not_a_time"),
    ],
)
async def test_a_capture_time_that_cannot_work_is_refused(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    kind: str,
    user_input: dict[str, str],
    error: str,
) -> None:
    """The form is shown again with an error."""
    report(hass, "30", CAPTURED)
    result = await _choose(hass, await _start(hass), kind)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == kind
    assert result["errors"] == {"base": error}


@pytest.mark.parametrize(
    ("details", "error"),
    [
        ({"unit_of_measurement": "kWh", "device_class": "battery"}, "unit_not_valid"),
        (
            {
                "unit_of_measurement": "%",
                "device_class": "battery",
                "state_class": "total_increasing",
            },
            "state_class_not_valid",
        ),
    ],
)
async def test_details_that_do_not_fit_the_device_class_are_refused(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    details: dict[str, str],
    error: str,
) -> None:
    """The form is shown again with an error."""
    report(hass, "30", CAPTURED)
    result = await _choose(hass, await _start(hass), "source_attribute")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"timestamp_attribute": "data_captured_at"}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], details)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "details"
    assert result["errors"] == {"base": error}


async def test_changed_options_reload_the_sensor_with_its_value(
    recorder_mock: Recorder, hass: HomeAssistant, frozen_now: FrozenDateTimeFactory
) -> None:
    """The value is restored after the reload and recorded after the unavailable row."""
    report(hass, "30", CAPTURED)
    entry = await _set_up_entry(hass)

    frozen_now.tick(60)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init"
    assert _suggested(result) == {"source": SOURCE}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"source": SOURCE}
    )
    assert result["step_id"] == "timestamp"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "source_attribute"}
    )
    assert _suggested(result) == {"timestamp_attribute": "data_captured_at"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"timestamp_attribute": "data_captured_at"}
    )
    assert result["step_id"] == "details"
    assert _suggested(result) == DETAILS
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {key: value for key, value in DETAILS.items() if key != "state_class"},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {
        key: value for key, value in OPTIONS.items() if key != "state_class"
    }
    state = hass.states.get(ENTITY)
    assert state.state == "30"
    assert "state_class" not in state.attributes
    assert state.attributes["captured_at"] == CAPTURED.isoformat()
    reloaded = NOW + timedelta(seconds=60)
    assert state.last_updated_timestamp == pytest.approx(reloaded.timestamp() + 0.001)

    await async_wait_recording_done(hass)
    rows = await hass.async_add_executor_job(
        lambda: get_significant_states(
            hass,
            NOW - timedelta(days=1),
            None,
            [ENTITY],
            include_start_time_state=False,
            significant_changes_only=False,
        )
    )
    assert [(row.state, row.last_updated) for row in rows[ENTITY]] == [
        ("30", CAPTURED),
        ("unavailable", reloaded),
        ("30", reloaded + timedelta(milliseconds=1)),
    ]


async def test_another_kind_of_capture_time_drops_the_previous_options(
    recorder_mock: Recorder, hass: HomeAssistant
) -> None:
    """Only the options of the chosen kind remain."""
    report(hass, "30", CAPTURED)
    hass.states.async_set(TIME_ENTITY, CAPTURED.isoformat(), {"at": "x"})
    entry = await _set_up_entry(hass)

    async def change(kind: str, *inputs: dict[str, Any]) -> None:
        options = hass.config_entries.options
        result = await options.async_init(entry.entry_id)
        result = await options.async_configure(result["flow_id"], {"source": SOURCE})
        result = await options.async_configure(
            result["flow_id"], {"next_step_id": kind}
        )
        for user_input in (*inputs, DETAILS):
            result = await options.async_configure(result["flow_id"], user_input)
        assert result["type"] is FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()

    await change(
        "entity_attribute",
        {"timestamp_entity": TIME_ENTITY},
        {"timestamp_attribute": "at"},
    )
    assert entry.options == {
        "name": OPTIONS["name"],
        "source": SOURCE,
        "timestamp_entity": TIME_ENTITY,
        "timestamp_attribute": "at",
        **DETAILS,
    }

    await change("template", {"timestamp_template": "{{ now() }}"})
    assert entry.options == {
        "name": OPTIONS["name"],
        "source": SOURCE,
        "timestamp_template": "{{ now() }}",
        **DETAILS,
    }


async def test_the_sensor_follows_its_renamed_source(
    recorder_mock: Recorder, hass: HomeAssistant, source_device: dr.DeviceEntry
) -> None:
    """A new entity ID of the source is stored in the options and followed."""
    report(hass, "30", CAPTURED)
    entry = await _set_up_entry(hass)

    er.async_get(hass).async_update_entity(
        SOURCE, new_entity_id="sensor.car_battery_level"
    )
    await hass.async_block_till_done()
    assert entry.options["source"] == "sensor.car_battery_level"

    hass.states.async_set(
        "sensor.car_battery_level",
        "40",
        {"data_captured_at": (NOW - timedelta(hours=1)).isoformat()},
    )
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == "40"
    assert er.async_get(hass).async_get(ENTITY).device_id == source_device.id


async def test_an_unloaded_sensor_is_unavailable(
    recorder_mock: Recorder, hass: HomeAssistant
) -> None:
    """Unloading the entry leaves the registered sensor unavailable."""
    report(hass, "30", CAPTURED)
    entry = await _set_up_entry(hass)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED
    assert hass.states.get(ENTITY).state == "unavailable"
