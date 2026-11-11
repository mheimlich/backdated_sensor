"""Dialogs to create a backdated sensor in the UI and to change its options."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

import probatio
from homeassistant.components.sensor import (
    CONF_STATE_CLASS,
    DEVICE_CLASS_STATE_CLASSES,
    DEVICE_CLASS_UNITS,
)
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.const import (
    CONF_DEVICE_CLASS,
    CONF_NAME,
    CONF_UNIT_OF_MEASUREMENT,
    Platform,
)
from homeassistant.helpers import selector
from homeassistant.helpers.schema_config_entry_flow import (
    SchemaCommonFlowHandler,
    SchemaConfigFlowHandler,
    SchemaFlowError,
    SchemaFlowFormStep,
)

from .const import CONF_SOURCE, CONF_TIMESTAMP_ATTRIBUTE, DOMAIN

SOURCE_SELECTOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain=SENSOR_DOMAIN)
)

UNITS = sorted(
    {str(unit) for units in DEVICE_CLASS_UNITS.values() for unit in units if unit}
)

# The source's state attributes that share their name with an option.
SOURCE_DETAILS = (CONF_UNIT_OF_MEASUREMENT, CONF_DEVICE_CLASS, CONF_STATE_CLASS)

USER_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_NAME): selector.TextSelector(),
        probatio.Required(CONF_SOURCE): SOURCE_SELECTOR,
    }
)

SOURCE_SCHEMA = probatio.Schema({probatio.Required(CONF_SOURCE): SOURCE_SELECTOR})


async def _details_schema(handler: SchemaCommonFlowHandler) -> probatio.Schema:
    """Return the form for the timestamp attribute, the unit and the classes."""
    return probatio.Schema(
        {
            probatio.Required(CONF_TIMESTAMP_ATTRIBUTE): selector.AttributeSelector(
                selector.AttributeSelectorConfig(entity_id=handler.options[CONF_SOURCE])
            ),
            probatio.Optional(CONF_UNIT_OF_MEASUREMENT): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=UNITS,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    custom_value=True,
                    sort=True,
                )
            ),
            probatio.Optional(CONF_DEVICE_CLASS): selector.DeviceClassSelector(
                selector.DeviceClassSelectorConfig(domain=Platform.SENSOR)
            ),
            probatio.Optional(CONF_STATE_CLASS): selector.StateClassSelector(),
        }
    )


async def _source_details(handler: SchemaCommonFlowHandler) -> dict[str, Any]:
    """Suggest the unit and the classes of the source."""
    state = handler.parent_handler.hass.states.get(handler.options[CONF_SOURCE])
    if state is None:
        return {}
    return {
        key: state.attributes[key] for key in SOURCE_DETAILS if key in state.attributes
    }


async def _validate_details(
    handler: SchemaCommonFlowHandler, user_input: dict[str, Any]
) -> dict[str, Any]:
    """Check that the unit and the state class fit the device class."""
    if device_class := user_input.get(CONF_DEVICE_CLASS):
        units = DEVICE_CLASS_UNITS.get(device_class)
        if units is not None and user_input.get(CONF_UNIT_OF_MEASUREMENT) not in units:
            raise SchemaFlowError("unit_not_valid")
        state_classes = DEVICE_CLASS_STATE_CLASSES.get(device_class)
        state_class = user_input.get(CONF_STATE_CLASS)
        if (
            state_classes is not None
            and state_class
            and state_class not in state_classes
        ):
            raise SchemaFlowError("state_class_not_valid")
    return user_input


CONFIG_FLOW = {
    "user": SchemaFlowFormStep(USER_SCHEMA, next_step="details"),
    "details": SchemaFlowFormStep(
        _details_schema,
        validate_user_input=_validate_details,
        suggested_values=_source_details,
    ),
}

OPTIONS_FLOW = {
    "init": SchemaFlowFormStep(SOURCE_SCHEMA, next_step="details"),
    "details": SchemaFlowFormStep(
        _details_schema, validate_user_input=_validate_details
    ),
}


class ConfigFlowHandler(SchemaConfigFlowHandler, domain=DOMAIN):
    """Create a backdated sensor in the UI and change its options."""

    config_flow = CONFIG_FLOW
    options_flow = OPTIONS_FLOW
    options_flow_reloads = True

    def async_config_entry_title(self, options: Mapping[str, Any]) -> str:
        """Name the entry, and so the sensor, as entered."""
        return cast(str, options[CONF_NAME])
