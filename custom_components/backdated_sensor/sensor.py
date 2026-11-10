"""Sensor that records another sensor's value at the time given by one of its attributes."""

from __future__ import annotations

import logging
import math
import time
from datetime import datetime, timedelta
from typing import Any

import voluptuous as vol
from homeassistant.components.sensor import (
    CONF_STATE_CLASS,
    DEVICE_CLASSES_SCHEMA,
    STATE_CLASSES_SCHEMA,
    SensorEntity,
)
from homeassistant.components.sensor import (
    PLATFORM_SCHEMA as SENSOR_PLATFORM_SCHEMA,
)
from homeassistant.const import (
    CONF_DEVICE_CLASS,
    CONF_NAME,
    CONF_UNIQUE_ID,
    CONF_UNIT_OF_MEASUREMENT,
    EVENT_HOMEASSISTANT_STOP,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.entity import EntityPlatformState
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import (
    async_track_point_in_utc_time,
    async_track_state_change_event,
)
from homeassistant.helpers.restore_state import (
    ExtraStoredData,
    RestoredExtraData,
    RestoreEntity,
)
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.util import dt as dt_util

from .const import ATTR_CAPTURED_AT, CONF_SOURCE, CONF_TIMESTAMP_ATTRIBUTE
from .statistics import async_correct_hourly_statistics, hour_start

_LOGGER = logging.getLogger(__name__)

# The recorder compiles an hour a few seconds after it ends.
CORRECTION_DELAY = timedelta(minutes=15)

# Spacing that keeps a state from sharing its time with the row recorded before it.
MIN_STEP = 0.001

PLATFORM_SCHEMA = SENSOR_PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_NAME): cv.string,
        vol.Required(CONF_SOURCE): cv.entity_id,
        vol.Required(CONF_TIMESTAMP_ATTRIBUTE): cv.string,
        vol.Optional(CONF_UNIQUE_ID): cv.string,
        vol.Optional(CONF_UNIT_OF_MEASUREMENT): cv.string,
        vol.Optional(CONF_DEVICE_CLASS): DEVICE_CLASSES_SCHEMA,
        vol.Optional(CONF_STATE_CLASS): STATE_CLASSES_SCHEMA,
    }
)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up a backdated sensor from YAML."""
    async_add_entities([BackdatedSensor(config)])


class BackdatedSensor(SensorEntity, RestoreEntity):
    """Mirrors a numeric sensor and records each value at its capture time."""

    _attr_should_poll = False

    def __init__(self, config: ConfigType) -> None:
        """Initialise the sensor from its YAML entry."""
        self._attr_name = config[CONF_NAME]
        self._attr_unique_id = config.get(CONF_UNIQUE_ID)
        self._attr_native_unit_of_measurement = config.get(CONF_UNIT_OF_MEASUREMENT)
        self._attr_device_class = config.get(CONF_DEVICE_CLASS)
        self._attr_state_class = config.get(CONF_STATE_CLASS)
        self._source: str = config[CONF_SOURCE]
        self._timestamp_attribute: str = config[CONF_TIMESTAMP_ATTRIBUTE]
        self._captured: datetime | None = None
        self._written = 0.0
        self._value_taken_over = False
        self._correct_from: datetime | None = None
        self._unsub_correction: CALLBACK_TYPE | None = None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the capture time of the value."""
        if self._captured is None:
            return None
        return {ATTR_CAPTURED_AT: self._captured.isoformat()}

    @property
    def extra_restore_state_data(self) -> ExtraStoredData:
        """Return what a restart needs to continue behind the recorded rows."""
        return RestoredExtraData(
            {
                "native_value": self._attr_native_value,
                "captured": _isoformat(self._captured),
                "written": self._written,
                "correct_statistics_from": _isoformat(self._correct_from),
            }
        )

    async def async_added_to_hass(self) -> None:
        """Restore the last value and follow the source."""
        await super().async_added_to_hass()
        if (extra := await self.async_get_last_extra_data()) is not None:
            self._restore(extra.as_dict())
        self._take_over(self.hass.states.get(self._source))
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, self._source, self._async_source_changed
            )
        )
        self.async_on_remove(self._cancel_correction)
        self.async_on_remove(
            self.hass.bus.async_listen(
                EVENT_HOMEASSISTANT_STOP, self._async_cancel_correction_at_stop
            )
        )
        if self._correct_from is not None:
            self._schedule_correction()

    def _restore(self, data: dict[str, Any]) -> None:
        value = data.get("native_value")
        captured = _parse_timestamp(data.get("captured"))
        if isinstance(value, int | float) and captured is not None:
            self._attr_native_value = value
            self._captured = captured
        if isinstance(written := data.get("written"), int | float):
            self._written = float(written)
        self._correct_from = _parse_timestamp(data.get("correct_statistics_from"))

    def _take_over(self, state: State | None) -> bool:
        """Take the source's value if its capture time is later than the current one."""
        if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return False
        captured = _parse_timestamp(state.attributes.get(self._timestamp_attribute))
        if captured is None or (
            self._captured is not None and captured <= self._captured
        ):
            return False
        if (value := _parse_number(state.state)) is None:
            _LOGGER.warning(
                "%s ignores the non-numeric state %r of %s",
                self.entity_id,
                state.state,
                self._source,
            )
            return False
        self._attr_native_value = value
        self._captured = captured
        self._value_taken_over = True
        return True

    @callback
    def _async_source_changed(self, event: Event[EventStateChangedData]) -> None:
        if self._take_over(event.data["new_state"]):
            self.async_write_ha_state()

    @callback
    def _async_write_ha_state(self) -> None:
        """Write the state with the value's capture time as its update time.

        The base class always writes the current time and offers no way to pass
        another, so this replaces it. It leaves out updating the entity registry's
        capabilities, which stay as configured, and the `customize` overrides.
        """
        if self._platform_state is not EntityPlatformState.ADDED:
            return
        calculated = self._async_calculate_state()
        timestamp = self._write_timestamp()
        self.hass.states.async_set(
            self.entity_id,
            calculated.state,
            calculated.attributes,
            force_update=self.force_update,
            context=self._context,
            state_info=self._state_info,
            timestamp=timestamp,
        )
        self._written = timestamp
        if self._value_taken_over:
            self._value_taken_over = False
            self._note_statistics_to_correct(timestamp)

    def _write_timestamp(self) -> float:
        """Return the capture time, but no earlier than the latest recorded row.

        Rows are read back in time order, so a state written before an existing row
        would show up as superseded by it.
        """
        now = time.time()
        timestamp = min(self._captured.timestamp(), now) if self._captured else now
        latest = self._written
        if (current := self.hass.states.get(self.entity_id)) is not None:
            latest = max(latest, current.last_updated_timestamp)
        if timestamp <= latest:
            timestamp = latest + MIN_STEP
        return timestamp

    def _note_statistics_to_correct(self, timestamp: float) -> None:
        """Schedule a correction if the recorder has compiled statistics since timestamp."""
        if self._attr_state_class is None:
            return
        compiled_until = math.floor(time.time() / 300) * 300
        if timestamp >= compiled_until:
            return
        start = hour_start(dt_util.utc_from_timestamp(timestamp))
        if self._correct_from is None or start < self._correct_from:
            self._correct_from = start
        self._schedule_correction()

    def _schedule_correction(self) -> None:
        """Correct once the hour now running has been compiled as well."""
        self._cancel_correction()
        when = hour_start(dt_util.utcnow()) + timedelta(hours=1) + CORRECTION_DELAY
        self._unsub_correction = async_track_point_in_utc_time(
            self.hass, self._async_correct_statistics, when
        )

    @callback
    def _cancel_correction(self) -> None:
        if self._unsub_correction is not None:
            self._unsub_correction()
            self._unsub_correction = None

    @callback
    def _async_cancel_correction_at_stop(self, _event: Event) -> None:
        """Leave a pending correction to the restore data, which keeps its start."""
        self._cancel_correction()

    async def _async_correct_statistics(self, _now: datetime) -> None:
        self._unsub_correction = None
        start, self._correct_from = self._correct_from, None
        if start is None:
            return
        end = hour_start(dt_util.utcnow())
        try:
            count = await async_correct_hourly_statistics(
                self.hass, self.entity_id, self.unit_of_measurement, start, end
            )
        except Exception:
            _LOGGER.exception(
                "Could not correct the statistics of %s from %s", self.entity_id, start
            )
            return
        _LOGGER.debug(
            "Corrected %d hourly statistics of %s from %s", count, self.entity_id, start
        )


def _parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return dt_util.as_utc(value)
    if isinstance(value, str) and (moment := dt_util.parse_datetime(value)):
        return dt_util.as_utc(moment)
    return None


def _parse_number(value: str) -> int | float | None:
    try:
        return int(value)
    except ValueError:
        pass
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _isoformat(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment is not None else None
