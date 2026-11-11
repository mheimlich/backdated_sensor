"""Where a backdated sensor reads the capture time of the source's value from."""

from __future__ import annotations

import logging
import math
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Any

import probatio
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.exceptions import TemplateError
from homeassistant.helpers.event import (
    TrackTemplate,
    TrackTemplateResult,
    TrackTemplateResultInfo,
    async_track_state_change_event,
    async_track_template_result,
)
from homeassistant.helpers.template import Template
from homeassistant.util import dt as dt_util

from .const import (
    CONF_TIMESTAMP_ATTRIBUTE,
    CONF_TIMESTAMP_DELAY,
    CONF_TIMESTAMP_ENTITY,
    CONF_TIMESTAMP_PROPERTY,
    CONF_TIMESTAMP_TEMPLATE,
    KIND_DELAY,
    KIND_ENTITY_ATTRIBUTE,
    KIND_ENTITY_CHANGE,
    KIND_ENTITY_STATE,
    KIND_SOURCE_ATTRIBUTE,
    KIND_TEMPLATE,
    KINDS,
    TIMESTAMP_KEYS,
)

_LOGGER = logging.getLogger(__name__)

# Larger numbers are taken as milliseconds; in seconds they lie after the year 5000.
MAX_UNIX_SECONDS = 1e11


class CaptureTime(ABC):
    """Reads the capture time of the source's current value."""

    @abstractmethod
    def read(self, hass: HomeAssistant, source: State) -> datetime | None:
        """Return the capture time, or None if there is none."""

    def waits(self, source_id: str) -> bool:
        """Return whether the time comes from other entities than the source.

        Their state changes arrive separately from the source's, so the sensor waits
        for both to settle before it pairs the value with the time.
        """
        return False

    def track(
        self, hass: HomeAssistant, source_id: str, action: Callable[[], None]
    ) -> CALLBACK_TYPE:
        """Call action when an entity other than the source may change the time."""
        return lambda: None


class SourceAttribute(CaptureTime):
    """An attribute of the source."""

    def __init__(self, attribute: str) -> None:
        """Read the time from the given attribute."""
        self.attribute = attribute

    def read(self, hass: HomeAssistant, source: State) -> datetime | None:
        """Return the attribute's time."""
        return parse_time(source.attributes.get(self.attribute))


class Delay(CaptureTime):
    """The time the source's state was received, less a fixed delay."""

    def __init__(self, delay: timedelta) -> None:
        """Subtract the given delay."""
        self.delay = delay

    def read(self, hass: HomeAssistant, source: State) -> datetime | None:
        """Return the reception time less the delay."""
        return source.last_updated - self.delay


class OtherEntity(CaptureTime):
    """Something of another entity, which is tracked."""

    def __init__(self, entity_id: str) -> None:
        """Read the time from the given entity."""
        self.entity_id = entity_id

    def read(self, hass: HomeAssistant, source: State) -> datetime | None:
        """Return the time read from the entity's current state."""
        state = hass.states.get(self.entity_id)
        return None if state is None else self.read_state(state)

    @abstractmethod
    def read_state(self, state: State) -> datetime | None:
        """Return the time read from the entity's state."""

    def waits(self, source_id: str) -> bool:
        """Wait unless the entity is the source itself."""
        return self.entity_id != source_id

    def track(
        self, hass: HomeAssistant, source_id: str, action: Callable[[], None]
    ) -> CALLBACK_TYPE:
        """Call action on every state change of the entity."""
        if self.entity_id == source_id:
            return lambda: None

        @callback
        def changed(_event: Event[EventStateChangedData]) -> None:
            action()

        return async_track_state_change_event(hass, self.entity_id, changed)


class EntityState(OtherEntity):
    """The state of another entity."""

    def read_state(self, state: State) -> datetime | None:
        """Return the time the state gives."""
        if state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return None
        return parse_time(state.state)


class EntityAttribute(OtherEntity):
    """An attribute of another entity."""

    def __init__(self, entity_id: str, attribute: str) -> None:
        """Read the time from the given attribute of the entity."""
        super().__init__(entity_id)
        self.attribute = attribute

    def read_state(self, state: State) -> datetime | None:
        """Return the attribute's time."""
        return parse_time(state.attributes.get(self.attribute))


class EntityChange(OtherEntity):
    """When another entity last changed, was updated or reported."""

    def __init__(self, entity_id: str, property_name: str) -> None:
        """Read last_changed, last_updated or last_reported of the entity."""
        super().__init__(entity_id)
        self.property_name = property_name

    def read_state(self, state: State) -> datetime | None:
        """Return the time the state carries."""
        return getattr(state, self.property_name)


class TemplateTime(CaptureTime):
    """A template, rendered with the variable `source` holding the source's entity ID.

    It is rendered when the source or an entity it reads changes, never because the
    clock moved on, so `now()` gives the time the change was received.
    """

    def __init__(self, template: str) -> None:
        """Render the given template."""
        self.template = template
        self._compiled: Template | None = None
        self._tracker: TrackTemplateResultInfo | None = None

    def _template(self, hass: HomeAssistant) -> Template:
        if self._compiled is None:
            self._compiled = Template(self.template, hass)
        return self._compiled

    def read(self, hass: HomeAssistant, source: State) -> datetime | None:
        """Return the time the template renders."""
        try:
            result = self._template(hass).async_render(
                {"source": source.entity_id}, parse_result=False
            )
        except TemplateError as err:
            _LOGGER.warning(
                "The capture time template %s failed: %s", self.template, err
            )
            return None
        return parse_time(result)

    def waits(self, source_id: str) -> bool:
        """Wait if the template read other entities than the source last time."""
        if self._tracker is None:
            return False
        listeners = self._tracker.listeners
        return bool(
            listeners["all"]
            or listeners["domains"]
            or listeners["entities"] - {source_id}
        )

    def track(
        self, hass: HomeAssistant, source_id: str, action: Callable[[], None]
    ) -> CALLBACK_TYPE:
        """Call action when an entity other than the source changes the result."""

        @callback
        def changed(
            event: Event[EventStateChangedData] | None,
            _updates: list[TrackTemplateResult],
        ) -> None:
            if event is not None and event.data["entity_id"] != source_id:
                action()

        tracker = async_track_template_result(
            hass,
            [TrackTemplate(self._template(hass), {"source": source_id})],
            changed,
        )
        self._tracker = tracker

        @callback
        def remove() -> None:
            tracker.async_remove()
            self._tracker = None

        return remove


def kind_of(config: Mapping[str, Any]) -> str:
    """Return how the options give the capture time.

    Raises probatio.Invalid unless they hold exactly one valid combination.
    """
    given = TIMESTAMP_KEYS & config.keys()
    for kind, keys in KINDS.items():
        if given == keys:
            return kind
    raise probatio.Invalid(
        "Give the capture time with timestamp_attribute, timestamp_entity (alone, "
        "with timestamp_attribute or with timestamp_property), timestamp_template "
        f"or timestamp_delay, not with {', '.join(sorted(given)) or 'nothing'}"
    )


FACTORIES: dict[str, Callable[[Mapping[str, Any]], CaptureTime]] = {
    KIND_SOURCE_ATTRIBUTE: lambda config: SourceAttribute(
        config[CONF_TIMESTAMP_ATTRIBUTE]
    ),
    KIND_ENTITY_STATE: lambda config: EntityState(config[CONF_TIMESTAMP_ENTITY]),
    KIND_ENTITY_ATTRIBUTE: lambda config: EntityAttribute(
        config[CONF_TIMESTAMP_ENTITY], config[CONF_TIMESTAMP_ATTRIBUTE]
    ),
    KIND_ENTITY_CHANGE: lambda config: EntityChange(
        config[CONF_TIMESTAMP_ENTITY], config[CONF_TIMESTAMP_PROPERTY]
    ),
    KIND_TEMPLATE: lambda config: TemplateTime(config[CONF_TIMESTAMP_TEMPLATE]),
    KIND_DELAY: lambda config: Delay(config[CONF_TIMESTAMP_DELAY]),
}


def capture_time_from(config: Mapping[str, Any]) -> CaptureTime:
    """Return the capture time the validated options describe."""
    return FACTORIES[kind_of(config)](config)


def parse_time(value: Any) -> datetime | None:
    """Return a datetime, ISO 8601 string or Unix time as an aware UTC datetime.

    A Unix time is in seconds, or in milliseconds if it is too large for seconds. A
    time without an offset is local time.
    """
    if isinstance(value, datetime):
        return dt_util.as_utc(value)
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return _from_unix(value)
    if not isinstance(value, str):
        return None
    text = value.strip()
    try:
        number = float(text)
    except ValueError:
        pass
    else:
        return _from_unix(number)
    if (moment := dt_util.parse_datetime(text)) is None:
        return None
    return dt_util.as_utc(moment)


def _from_unix(number: float) -> datetime | None:
    if not math.isfinite(number) or number <= 0:
        return None
    seconds = number / 1000 if number >= MAX_UNIX_SECONDS else number
    try:
        return dt_util.utc_from_timestamp(seconds)
    except OverflowError, OSError, ValueError:
        return None
