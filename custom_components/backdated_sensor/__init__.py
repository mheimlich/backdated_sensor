"""Sensors and binary sensors that record another entity's state at its capture time."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.device import async_entity_id_to_device_id
from homeassistant.helpers.helper_integration import (
    async_handle_source_entity_changes,
)

from .const import CONF_SOURCE, DOMAIN

CONFIG_SCHEMA = cv.platform_only_config_schema(DOMAIN)

PLATFORMS = (Platform.BINARY_SENSOR, Platform.SENSOR)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a backdated sensor created in the UI."""

    @callback
    def set_source(source: str) -> None:
        """Follow the source to its new entity ID."""
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, CONF_SOURCE: source}
        )
        hass.config_entries.async_schedule_reload(entry.entry_id)

    entry.async_on_unload(
        async_handle_source_entity_changes(
            hass,
            helper_config_entry_id=entry.entry_id,
            set_source_entity_id_or_uuid=set_source,
            source_device_id=async_entity_id_to_device_id(
                hass, entry.options[CONF_SOURCE]
            ),
            source_entity_id_or_uuid=entry.options[CONF_SOURCE],
        )
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a backdated sensor created in the UI."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
