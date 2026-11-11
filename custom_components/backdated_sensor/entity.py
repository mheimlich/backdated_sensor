"""The entity the sensor and the binary sensor build on."""

from __future__ import annotations

import logging
import math
import time
from datetime import datetime, timedelta
from typing import Any

from homeassistant.const import (
    CONF_NAME,
    EVENT_HOMEASSISTANT_STOP,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    Platform,
)
from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    State,
    callback,
    split_entity_id,
)
from homeassistant.helpers.device_registry import AnyDeviceEntry
from homeassistant.helpers.entity import EntityPlatformState
from homeassistant.helpers.event import (
    async_call_later,
    async_track_point_in_utc_time,
    async_track_state_change_event,
)
from homeassistant.helpers.restore_state import (
    ExtraStoredData,
    RestoredExtraData,
    RestoreEntity,
)
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from .capture_time import capture_time_from, parse_time
from .const import ATTR_CAPTURED_AT, CONF_SOURCE
from .statistics import async_correct_hourly_statistics, hour_start

_LOGGER = logging.getLogger(__name__)

# The recorder compiles an hour a few seconds after it ends.
CORRECTION_DELAY = timedelta(minutes=15)

# Spacing that keeps a state from sharing its time with the row recorded before it.
MIN_STEP = 0.001

# How long the source and the entities giving the time have to stay unchanged before
# the value is paired with the time. An integration writes the states of one report
# within a fraction of a second.
SETTLE_DELAY = 5


def source_platform(source_id: str) -> Platform:
    """Return the platform of the entity mirroring the source: like the source's."""
    if split_entity_id(source_id)[0] == Platform.BINARY_SENSOR:
        return Platform.BINARY_SENSOR
    return Platform.SENSOR


class BackdatedEntity(RestoreEntity):
    """Mirrors the source's state and records each value at its capture time.

    A platform provides how a state becomes a value and how the value is set.
    """

    _attr_should_poll = False

    def __init__(
        self,
        config: ConfigType,
        unique_id: str | None,
        device: AnyDeviceEntry | None = None,
    ) -> None:
        """Initialise the entity from its options."""
        self._attr_name = config[CONF_NAME]
        self._attr_unique_id = unique_id
        self.device_entry = device
        self._source: str = config[CONF_SOURCE]
        self._capture_time = capture_time_from(config)
        self._captured: datetime | None = None
        self._written = 0.0
        self._value_taken_over = False
        self._correct_from: datetime | None = None
        self._unsub_correction: CALLBACK_TYPE | None = None
        self._unsub_settle: CALLBACK_TYPE | None = None
        self._unsub_capture_time: CALLBACK_TYPE | None = None

    def _value_from(self, raw: Any) -> Any:
        """Return a state or a stored value as this entity's value, or None."""
        raise NotImplementedError

    def _set_value(self, value: Any, source: State | None) -> None:
        """Show the value, taken from the source's state or, if None, restored."""
        raise NotImplementedError

    def _stored_value(self) -> Any:
        """Return the value in a form the restore data can hold."""
        raise NotImplementedError

    def _stored_details(self) -> dict[str, Any]:
        """Return what the restore data has to hold beside the value."""
        return {}

    def _restore_details(self, data: dict[str, Any]) -> None:
        """Restore what _stored_details returned, before the value."""

    def _keeps_statistics(self) -> bool:
        """Return whether the recorder compiles long-term statistics of the entity."""
        return False

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
                "native_value": self._stored_value(),
                "captured": _isoformat(self._captured),
                "written": self._written,
                "correct_statistics_from": _isoformat(self._correct_from),
                **self._stored_details(),
            }
        )

    async def async_added_to_hass(self) -> None:
        """Restore the last value and follow the source and the capture time."""
        await super().async_added_to_hass()
        if (extra := await self.async_get_last_extra_data()) is not None:
            self._restore(extra.as_dict())
        self._take_over(self.hass.states.get(self._source))
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, self._source, self._async_source_changed
            )
        )
        self._unsub_capture_time = self._capture_time.track(
            self.hass, self._source, self._async_changed
        )
        self.async_on_remove(self._cancel_timers)
        self.async_on_remove(
            self.hass.bus.async_listen(
                EVENT_HOMEASSISTANT_STOP, self._async_cancel_timers_at_stop
            )
        )
        if self._correct_from is not None:
            self._schedule_correction()

    def _restore(self, data: dict[str, Any]) -> None:
        self._restore_details(data)
        captured = parse_time(data.get("captured"))
        raw = data.get("native_value")
        if (
            captured is not None
            and raw is not None
            and (value := self._value_from(raw)) is not None
        ):
            self._set_value(value, None)
            self._captured = captured
        if isinstance(written := data.get("written"), int | float):
            self._written = float(written)
        self._correct_from = parse_time(data.get("correct_statistics_from"))

    def _take_over(self, state: State | None) -> bool:
        """Take the source's value if its capture time is later than the current one."""
        if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return False
        captured = self._capture_time.read(self.hass, state)
        if captured is None or (
            self._captured is not None and captured <= self._captured
        ):
            return False
        if (value := self._value_from(state.state)) is None:
            _LOGGER.warning(
                "%s cannot show the state %r of %s and ignores it",
                self.entity_id,
                state.state,
                self._source,
            )
            return False
        self._set_value(value, state)
        self._captured = captured
        self._value_taken_over = True
        return True

    @callback
    def _async_source_changed(self, _event: Event[EventStateChangedData]) -> None:
        self._async_changed()

    @callback
    def _async_changed(self) -> None:
        """Take the source's value now, or once the other entities have settled."""
        if not self._capture_time.waits(self._source):
            self._async_take_over_current()
            return
        self._cancel_settle()
        self._unsub_settle = async_call_later(
            self.hass, SETTLE_DELAY, self._async_settled
        )

    @callback
    def _async_settled(self, _now: datetime) -> None:
        self._unsub_settle = None
        self._async_take_over_current()

    @callback
    def _async_take_over_current(self) -> None:
        if self._take_over(self.hass.states.get(self._source)):
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
        if not self._keeps_statistics():
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
    def _cancel_settle(self) -> None:
        if self._unsub_settle is not None:
            self._unsub_settle()
            self._unsub_settle = None

    @callback
    def _cancel_timers(self) -> None:
        """Cancel the timers and the capture time's tracking, which may hold one."""
        self._cancel_correction()
        self._cancel_settle()
        if self._unsub_capture_time is not None:
            self._unsub_capture_time()
            self._unsub_capture_time = None

    @callback
    def _async_cancel_timers_at_stop(self, _event: Event) -> None:
        """Cancel the timers, whose work the next start picks up.

        The restore data keeps the start of a pending correction, and the entity
        takes the source's value when it is set up again. A template reading `now()`
        keeps a timer of its own.
        """
        self._cancel_timers()

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


def _isoformat(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment is not None else None
