"""Binary sensor that records another one's state at the time the state was captured."""

from __future__ import annotations

from typing import Any

import probatio
from homeassistant.components.binary_sensor import (
    DEVICE_CLASSES_SCHEMA,
    BinarySensorEntity,
)
from homeassistant.components.binary_sensor import (
    PLATFORM_SCHEMA as BINARY_SENSOR_PLATFORM_SCHEMA,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_DEVICE_CLASS, CONF_NAME, CONF_UNIQUE_ID, Platform
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
from .values import binary_value

BINARY_SENSOR_OPTIONS = {
    probatio.Required(CONF_SOURCE): cv.entity_id,
    **CAPTURE_TIME_OPTIONS,
    probatio.Optional(CONF_DEVICE_CLASS): DEVICE_CLASSES_SCHEMA,
}

PLATFORM_SCHEMA = probatio.All(
    BINARY_SENSOR_PLATFORM_SCHEMA.extend(
        {
            probatio.Required(CONF_NAME): cv.string,
            probatio.Optional(CONF_UNIQUE_ID): cv.string,
            **BINARY_SENSOR_OPTIONS,
        }
    ),
    one_capture_time,
)

ENTRY_OPTIONS_SCHEMA = probatio.All(
    probatio.Schema(BINARY_SENSOR_OPTIONS, extra=probatio.REMOVE_EXTRA),
    one_capture_time,
)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up a backdated binary sensor from YAML."""
    async_add_entities([BackdatedBinarySensor(config, config.get(CONF_UNIQUE_ID))])


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up a backdated binary sensor created in the UI, if its source is binary."""
    if source_platform(entry.options[CONF_SOURCE]) is not Platform.BINARY_SENSOR:
        return
    options = ENTRY_OPTIONS_SCHEMA(dict(entry.options))
    async_add_entities(
        [
            BackdatedBinarySensor(
                {**options, CONF_NAME: entry.title},
                entry.entry_id,
                async_entity_id_to_device(hass, options[CONF_SOURCE]),
            )
        ]
    )


class BackdatedBinarySensor(BackdatedEntity, BinarySensorEntity):
    """Mirrors an `on` or `off` state at its capture time."""

    def __init__(
        self,
        config: ConfigType,
        unique_id: str | None,
        device: AnyDeviceEntry | None = None,
    ) -> None:
        """Initialise the binary sensor from its options."""
        super().__init__(config, unique_id, device)
        self._attr_device_class = config.get(CONF_DEVICE_CLASS)

    def _value_from(self, raw: Any) -> bool | None:
        """Return `on` or `off` as True or False."""
        return binary_value(raw)

    def _set_value(self, value: Any, source: State | None) -> None:
        """Show the value."""
        self._attr_is_on = value

    def _stored_value(self) -> bool | None:
        """Return the value."""
        return self._attr_is_on
