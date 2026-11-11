"""Constants of the Backdated Sensor integration."""

DOMAIN = "backdated_sensor"

CONF_SOURCE = "source"
CONF_TIMESTAMP_ATTRIBUTE = "timestamp_attribute"
CONF_TIMESTAMP_DELAY = "timestamp_delay"
CONF_TIMESTAMP_ENTITY = "timestamp_entity"
CONF_TIMESTAMP_PROPERTY = "timestamp_property"
CONF_TIMESTAMP_TEMPLATE = "timestamp_template"

ATTR_CAPTURED_AT = "captured_at"

# The ways to give the capture time, with the options each one takes.
KIND_SOURCE_ATTRIBUTE = "source_attribute"
KIND_ENTITY_STATE = "entity_state"
KIND_ENTITY_ATTRIBUTE = "entity_attribute"
KIND_ENTITY_CHANGE = "entity_change"
KIND_TEMPLATE = "template"
KIND_DELAY = "delay"
KINDS = {
    KIND_SOURCE_ATTRIBUTE: frozenset({CONF_TIMESTAMP_ATTRIBUTE}),
    KIND_ENTITY_STATE: frozenset({CONF_TIMESTAMP_ENTITY}),
    KIND_ENTITY_ATTRIBUTE: frozenset({CONF_TIMESTAMP_ENTITY, CONF_TIMESTAMP_ATTRIBUTE}),
    KIND_ENTITY_CHANGE: frozenset({CONF_TIMESTAMP_ENTITY, CONF_TIMESTAMP_PROPERTY}),
    KIND_TEMPLATE: frozenset({CONF_TIMESTAMP_TEMPLATE}),
    KIND_DELAY: frozenset({CONF_TIMESTAMP_DELAY}),
}
TIMESTAMP_KEYS = frozenset().union(*KINDS.values())

PROPERTIES = ("last_changed", "last_updated", "last_reported")
