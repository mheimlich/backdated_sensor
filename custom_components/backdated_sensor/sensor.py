"""Sensor that records another sensor's value at the time the value was captured."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import probatio
from homeassistant.components.sensor import (
    ATTR_OPTIONS,
    CONF_STATE_CLASS,
    DEVICE_CLASSES_SCHEMA,
    STATE_CLASSES_SCHEMA,
    SensorDeviceClass,
    SensorEntity,
)
from homeassistant.components.sensor import (
    PLATFORM_SCHEMA as SENSOR_PLATFORM_SCHEMA,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_DEVICE_CLASS,
    CONF_NAME,
    CONF_UNIQUE_ID,
    CONF_UNIT_OF_MEASUREMENT,
    Platform,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.device import async_entity_id_to_device
from homeassistant.helpers.device_registry import AnyDeviceEntry
from homeassistant.helpers.entity_platform import (
    AddConfigEntryEntitiesCallback,
    AddEntitiesCallback,
)
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from .capture_time import CAPTURE_TIME_OPTIONS, one_capture_time
from .const import CONF_SOURCE
from .entity import BackdatedEntity, source_platform
from .values import numeric_expected, sensor_value

SENSOR_OPTIONS = {
    probatio.Required(CONF_SOURCE): cv.entity_id,
    **CAPTURE_TIME_OPTIONS,
    probatio.Optional(CONF_UNIT_OF_MEASUREMENT): cv.string,
    probatio.Optional(CONF_DEVICE_CLASS): DEVICE_CLASSES_SCHEMA,
    probatio.Optional(CONF_STATE_CLASS): STATE_CLASSES_SCHEMA,
}

PLATFORM_SCHEMA = probatio.All(
    SENSOR_PLATFORM_SCHEMA.extend(
        {
            probatio.Required(CONF_NAME): cv.string,
            probatio.Optional(CONF_UNIQUE_ID): cv.string,
            **SENSOR_OPTIONS,
        }
    ),
    one_capture_time,
)

ENTRY_OPTIONS_SCHEMA = probatio.All(
    probatio.Schema(SENSOR_OPTIONS, extra=probatio.REMOVE_EXTRA), one_capture_time
)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up a backdated sensor from YAML."""
    async_add_entities([BackdatedSensor(config, config.get(CONF_UNIQUE_ID))])


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up a backdated sensor created in the UI, unless its source is binary."""
    if source_platform(entry.options[CONF_SOURCE]) is not Platform.SENSOR:
        return
    options = ENTRY_OPTIONS_SCHEMA(dict(entry.options))
    async_add_entities(
        [
            BackdatedSensor(
                {**options, CONF_NAME: entry.title},
                entry.entry_id,
                async_entity_id_to_device(hass, options[CONF_SOURCE]),
            )
        ]
    )


class BackdatedSensor(BackdatedEntity, SensorEntity):
    """Mirrors a sensor's number, text, time or date at its capture time.

    An enum sensor takes the source's options along with each value.
    """

    def __init__(
        self,
        config: ConfigType,
        unique_id: str | None,
        device: AnyDeviceEntry | None = None,
    ) -> None:
        """Initialise the sensor from its options."""
        super().__init__(config, unique_id, device)
        self._attr_native_unit_of_measurement = config.get(CONF_UNIT_OF_MEASUREMENT)
        self._attr_device_class = config.get(CONF_DEVICE_CLASS)
        self._attr_state_class = config.get(CONF_STATE_CLASS)
        self._numeric = numeric_expected(
            self._attr_device_class,
            self._attr_state_class,
            self._attr_native_unit_of_measurement,
        )

    def _value_from(self, raw: Any) -> Any:
        """Return the state or stored value as fits the sensor's settings."""
        return sensor_value(raw, self._attr_device_class, self._numeric)

    def _set_value(self, value: Any, source: State | None) -> None:
        """Show the value, with the source's options if the sensor is an enum."""
        self._attr_native_value = value
        if (
            source is not None
            and self._attr_device_class == SensorDeviceClass.ENUM
            and isinstance(options := source.attributes.get(ATTR_OPTIONS), list)
        ):
            self._attr_options = [str(option) for option in options]

    def _stored_value(self) -> Any:
        """Return the value, a time or date as ISO 8601 text."""
        value = self._attr_native_value
        if isinstance(value, datetime | date):
            return value.isoformat()
        return value

    def _stored_details(self) -> dict[str, Any]:
        """Return the options of an enum sensor."""
        return {ATTR_OPTIONS: getattr(self, "_attr_options", None)}

    def _restore_details(self, data: dict[str, Any]) -> None:
        """Restore the options of an enum sensor."""
        if self._attr_device_class == SensorDeviceClass.ENUM and isinstance(
            options := data.get(ATTR_OPTIONS), list
        ):
            self._attr_options = options

    def _keeps_statistics(self) -> bool:
        """Return whether the sensor has a state class."""
        return self._attr_state_class is not None
