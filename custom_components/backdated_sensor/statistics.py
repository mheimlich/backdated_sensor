"""Correction of statistics compiled before a backdated value was recorded."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from functools import partial

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.db_schema import StatisticsShortTerm
from homeassistant.components.recorder.history import state_changes_during_period
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_import_statistics,
    get_metadata,
    statistics_during_period,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

HOUR = timedelta(hours=1)
PERIODS = {"5minute": timedelta(minutes=5), "hour": HOUR}


def hour_start(moment: datetime) -> datetime:
    """Return the start of the UTC hour containing moment."""
    return dt_util.as_utc(moment).replace(minute=0, second=0, microsecond=0)


def period_statistics(
    values: list[tuple[float, datetime]], start: datetime, end: datetime
) -> StatisticData | None:
    """Return the time-weighted mean, the minimum and the maximum of a step series.

    values holds (value, time) pairs in ascending time order. As in the recorder's
    compile, the last value before start counts from start, and a period without one
    is averaged from its first value on.
    """
    window: list[tuple[float, datetime]] = []
    for value, moment in values:
        if moment <= start:
            window = [(value, start)]
        elif moment < end:
            window.append((value, moment))
        else:
            break
    if not window:
        return None
    ends = [moment for _, moment in window[1:]] + [end]
    accumulated = sum(
        value * (until - moment).total_seconds()
        for (value, moment), until in zip(window, ends, strict=True)
    )
    numbers = [value for value, _ in window]
    return StatisticData(
        start=start,
        mean=accumulated / (end - window[0][1]).total_seconds(),
        min=min(numbers),
        max=max(numbers),
    )


async def async_correct_statistics(
    hass: HomeAssistant,
    statistic_id: str,
    unit: str | None,
    start: datetime,
    end: datetime,
) -> dict[str, int]:
    """Recompute the periods from start to end of a mean statistic from the recorded states.

    Both the five-minute and the hourly statistics are rewritten, but only periods the
    recorder has already compiled, so that its own compile never meets a row it did not
    write. Returns the number of periods rewritten by period length.
    """
    rewritten = dict.fromkeys(PERIODS, 0)
    if start >= end:
        return rewritten
    instance = get_instance(hass)
    found = await instance.async_add_executor_job(
        partial(get_metadata, hass, statistic_ids={statistic_id})
    )
    if (entry := found.get(statistic_id)) is None:
        return rewritten
    stored = entry[1]
    if (
        stored["mean_type"] != StatisticMeanType.ARITHMETIC
        or stored["has_sum"]
        or stored["unit_of_measurement"] != unit
    ):
        return rewritten

    compiled: dict[str, list[datetime]] = {}
    for period in PERIODS:
        rows = await instance.async_add_executor_job(
            statistics_during_period,
            hass,
            start,
            end,
            {statistic_id},
            period,
            None,
            {"mean"},
        )
        compiled[period] = [
            dt_util.utc_from_timestamp(row["start"])
            for row in rows.get(statistic_id, [])
        ]
    if not any(compiled.values()):
        return rewritten

    # The query leaves out a row recorded exactly at its start, so it starts a
    # microsecond earlier, as the recorder's own compile does.
    states = await instance.async_add_executor_job(
        partial(
            state_changes_during_period,
            hass,
            start - timedelta.resolution,
            end,
            statistic_id,
            no_attributes=True,
            include_start_time_state=True,
        )
    )
    values = [
        (value, state.last_updated)
        for state in states.get(statistic_id, [])
        if (value := _to_float(state.state)) is not None
    ]
    metadata = StatisticMetaData(
        mean_type=StatisticMeanType.ARITHMETIC,
        has_sum=False,
        name=stored["name"],
        source=stored["source"],
        statistic_id=statistic_id,
        unit_class=stored["unit_class"],
        unit_of_measurement=stored["unit_of_measurement"],
    )
    for period, length in PERIODS.items():
        rows = [
            row
            for moment in compiled[period]
            if (row := period_statistics(values, moment, moment + length)) is not None
        ]
        if not rows:
            continue
        if period == "hour":
            async_import_statistics(hass, metadata, rows)
        else:
            # Home Assistant has no public import of five-minute statistics.
            instance.async_import_statistics(metadata, rows, StatisticsShortTerm)
        rewritten[period] = len(rows)
    return rewritten


def _to_float(state: str) -> float | None:
    try:
        number = float(state)
    except ValueError:
        return None
    return number if math.isfinite(number) else None
