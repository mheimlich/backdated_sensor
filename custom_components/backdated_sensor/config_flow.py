"""Dialogs to create a backdated sensor in the UI and to change its options."""

from __future__ import annotations

from collections.abc import Callable, Coroutine, Mapping
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
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    Platform,
)
from homeassistant.exceptions import TemplateError
from homeassistant.helpers import selector
from homeassistant.helpers.schema_config_entry_flow import (
    SchemaCommonFlowHandler,
    SchemaConfigFlowHandler,
    SchemaFlowError,
    SchemaFlowFormStep,
    SchemaFlowMenuStep,
    SchemaFlowStep,
)
from homeassistant.helpers.template import Template
from homeassistant.helpers.typing import UNDEFINED, UndefinedType

from .capture_time import parse_time
from .const import (
    CONF_SOURCE,
    CONF_TIMESTAMP_ATTRIBUTE,
    CONF_TIMESTAMP_DELAY,
    CONF_TIMESTAMP_ENTITY,
    CONF_TIMESTAMP_PROPERTY,
    CONF_TIMESTAMP_TEMPLATE,
    DOMAIN,
    KIND_DELAY,
    KIND_ENTITY_ATTRIBUTE,
    KIND_ENTITY_CHANGE,
    KIND_ENTITY_STATE,
    KIND_SOURCE_ATTRIBUTE,
    KIND_TEMPLATE,
    KINDS,
    PROPERTIES,
    TIMESTAMP_KEYS,
)

type Validator = Callable[
    [SchemaCommonFlowHandler, dict[str, Any]], Coroutine[Any, Any, dict[str, Any]]
]
type Suggestions = (
    Callable[[SchemaCommonFlowHandler], Coroutine[Any, Any, dict[str, Any]]]
    | UndefinedType
)

SOURCE_SELECTOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain=SENSOR_DOMAIN)
)

UNITS = sorted(
    {str(unit) for units in DEVICE_CLASS_UNITS.values() for unit in units if unit}
)

# The source's state attributes that share their name with an option.
SOURCE_DETAILS = (CONF_UNIT_OF_MEASUREMENT, CONF_DEVICE_CLASS, CONF_STATE_CLASS)

# Template results that may only show that the entities it reads have no value yet.
NO_TIME_YET = ("", "None", STATE_UNKNOWN, STATE_UNAVAILABLE)

USER_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_NAME): selector.TextSelector(),
        probatio.Required(CONF_SOURCE): SOURCE_SELECTOR,
    }
)

SOURCE_SCHEMA = probatio.Schema({probatio.Required(CONF_SOURCE): SOURCE_SELECTOR})

ENTITY_SCHEMA = probatio.Schema(
    {probatio.Required(CONF_TIMESTAMP_ENTITY): selector.EntitySelector()}
)

ENTITY_CHANGE_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_TIMESTAMP_ENTITY): selector.EntitySelector(),
        probatio.Required(
            CONF_TIMESTAMP_PROPERTY, default=PROPERTIES[0]
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=list(PROPERTIES), translation_key=CONF_TIMESTAMP_PROPERTY
            )
        ),
    }
)

TEMPLATE_SCHEMA = probatio.Schema(
    {probatio.Required(CONF_TIMESTAMP_TEMPLATE): selector.TemplateSelector()}
)

DELAY_SCHEMA = probatio.Schema(
    {probatio.Required(CONF_TIMESTAMP_DELAY): selector.DurationSelector()}
)


def _attribute_schema(
    entity_key: str,
) -> Callable[[SchemaCommonFlowHandler], Coroutine[Any, Any, probatio.Schema]]:
    """Return a form offering the attributes of the entity chosen under entity_key."""

    async def schema(handler: SchemaCommonFlowHandler) -> probatio.Schema:
        return probatio.Schema(
            {
                probatio.Required(CONF_TIMESTAMP_ATTRIBUTE): selector.AttributeSelector(
                    selector.AttributeSelectorConfig(
                        entity_id=handler.options[entity_key]
                    )
                )
            }
        )

    return schema


async def _details_schema(handler: SchemaCommonFlowHandler) -> probatio.Schema:
    """Return the form for the unit and the classes."""
    return probatio.Schema(
        {
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


async def _check_not_source(
    handler: SchemaCommonFlowHandler, user_input: dict[str, Any]
) -> None:
    """Refuse the source, whose state is the value."""
    if user_input[CONF_TIMESTAMP_ENTITY] == handler.options[CONF_SOURCE]:
        raise SchemaFlowError("entity_is_source")


async def _check_template(
    handler: SchemaCommonFlowHandler, user_input: dict[str, Any]
) -> None:
    """Render the template once and refuse it if it fails or gives no time."""
    template = Template(
        user_input[CONF_TIMESTAMP_TEMPLATE], handler.parent_handler.hass
    )
    try:
        result = template.async_render(
            {"source": handler.options[CONF_SOURCE]}, parse_result=False
        )
    except TemplateError as err:
        raise SchemaFlowError("template_invalid") from err
    if result.strip() not in NO_TIME_YET and parse_time(result) is None:
        raise SchemaFlowError("template_not_a_time")


def _choosing(
    kind: str,
    check: Callable[
        [SchemaCommonFlowHandler, dict[str, Any]], Coroutine[Any, Any, None]
    ]
    | None = None,
) -> Validator:
    """Return a validator that drops the options of the other kinds of capture time."""

    async def validate(
        handler: SchemaCommonFlowHandler, user_input: dict[str, Any]
    ) -> dict[str, Any]:
        if check is not None:
            await check(handler, user_input)
        for key in TIMESTAMP_KEYS - KINDS[kind]:
            handler.options.pop(key, None)
        return user_input

    return validate


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


def _capture_time_steps(details: Suggestions) -> dict[str, SchemaFlowStep]:
    """Return the steps choosing the capture time, followed by the unit and classes."""
    return {
        "timestamp": SchemaFlowMenuStep(list(KINDS)),
        KIND_SOURCE_ATTRIBUTE: SchemaFlowFormStep(
            _attribute_schema(CONF_SOURCE),
            validate_user_input=_choosing(KIND_SOURCE_ATTRIBUTE),
            next_step="details",
        ),
        KIND_ENTITY_STATE: SchemaFlowFormStep(
            ENTITY_SCHEMA,
            validate_user_input=_choosing(KIND_ENTITY_STATE, _check_not_source),
            next_step="details",
        ),
        KIND_ENTITY_ATTRIBUTE: SchemaFlowFormStep(
            ENTITY_SCHEMA,
            validate_user_input=_choosing(KIND_ENTITY_ATTRIBUTE),
            next_step="entity_attribute_name",
        ),
        "entity_attribute_name": SchemaFlowFormStep(
            _attribute_schema(CONF_TIMESTAMP_ENTITY), next_step="details"
        ),
        KIND_ENTITY_CHANGE: SchemaFlowFormStep(
            ENTITY_CHANGE_SCHEMA,
            validate_user_input=_choosing(KIND_ENTITY_CHANGE),
            next_step="details",
        ),
        KIND_TEMPLATE: SchemaFlowFormStep(
            TEMPLATE_SCHEMA,
            validate_user_input=_choosing(KIND_TEMPLATE, _check_template),
            next_step="details",
        ),
        KIND_DELAY: SchemaFlowFormStep(
            DELAY_SCHEMA,
            validate_user_input=_choosing(KIND_DELAY),
            next_step="details",
        ),
        "details": SchemaFlowFormStep(
            _details_schema,
            validate_user_input=_validate_details,
            suggested_values=details,
        ),
    }


CONFIG_FLOW = {
    "user": SchemaFlowFormStep(USER_SCHEMA, next_step="timestamp"),
    **_capture_time_steps(_source_details),
}

OPTIONS_FLOW = {
    "init": SchemaFlowFormStep(SOURCE_SCHEMA, next_step="timestamp"),
    **_capture_time_steps(UNDEFINED),
}


class ConfigFlowHandler(SchemaConfigFlowHandler, domain=DOMAIN):
    """Create a backdated sensor in the UI and change its options."""

    config_flow = CONFIG_FLOW
    options_flow = OPTIONS_FLOW
    options_flow_reloads = True

    def async_config_entry_title(self, options: Mapping[str, Any]) -> str:
        """Name the entry, and so the sensor, as entered."""
        return cast(str, options[CONF_NAME])
