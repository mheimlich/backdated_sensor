"""How a source's state, or a stored value, becomes the value of a backdated entity."""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any

from homeassistant.components.sensor import (
    NON_NUMERIC_DEVICE_CLASSES,
    SensorDeviceClass,
)
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.util import dt as dt_util

from .capture_time import parse_time


def numeric_expected(
    device_class: str | None, state_class: str | None, unit: str | None
) -> bool:
    """Return whether a sensor with these settings needs a number, as Home Assistant does."""
    if device_class in NON_NUMERIC_DEVICE_CLASSES:
        return False
    return state_class is not None or unit is not None or device_class is not None


def sensor_value(
    raw: Any, device_class: str | None, numeric: bool
) -> int | float | str | datetime | date | None:
    """Return a state or stored value as a sensor value, or None if it cannot be one.

    A timestamp or uptime sensor takes a time, a date sensor a date, a numeric sensor
    a finite number and any other sensor a text.
    """
    if device_class in (SensorDeviceClass.TIMESTAMP, SensorDeviceClass.UPTIME):
        return parse_time(raw)
    if device_class == SensorDeviceClass.DATE:
        return dt_util.parse_date(raw) if isinstance(raw, str) else None
    if numeric:
        return parse_number(raw)
    return raw if isinstance(raw, str) else None


def binary_value(raw: Any) -> bool | None:
    """Return `on`, `off` or a stored bool as a binary sensor value, or None."""
    if isinstance(raw, bool):
        return raw
    return {STATE_ON: True, STATE_OFF: False}.get(raw)


def parse_number(raw: Any) -> int | float | None:
    """Return a finite number given as a number or a text, or None."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return raw if math.isfinite(raw) else None
    if not isinstance(raw, str):
        return None
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        number = float(raw)
    except ValueError:
        return None
    return number if math.isfinite(number) else None
