"""Tests of the correction of statistics after a backdated value."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.recorder import Recorder
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import HomeAssistant, State
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    async_mock_restore_state_shutdown_restart,
    mock_restore_cache_with_extra_data,
)
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
    do_adhoc_statistics,
)

from custom_components.backdated_sensor.statistics import period_statistics

from .conftest import ENTITY, report, setup_sensor

DAY = datetime(2026, 11, 10, tzinfo=UTC)


def _at(hour: int, minute: int = 0) -> datetime:
    return DAY + timedelta(hours=hour, minutes=minute)


async def _compile(hass: HomeAssistant, start: datetime, end: datetime) -> None:
    """Compile the five-minute periods from start to end, and the hours they complete."""
    moment = start
    while moment < end:
        do_adhoc_statistics(hass, start=moment)
        moment += timedelta(minutes=5)
    await async_wait_recording_done(hass)


async def _hours(hass: HomeAssistant) -> dict[int, tuple[float, float, float]]:
    """Return the hourly (mean, min, max) of the sensor by hour of DAY."""
    await async_wait_recording_done(hass)
    rows = await hass.async_add_executor_job(
        statistics_during_period,
        hass,
        DAY,
        None,
        {ENTITY},
        "hour",
        None,
        {"mean", "min", "max"},
    )
    return {
        datetime.fromtimestamp(row["start"], UTC).hour: (
            pytest.approx(row["mean"]),
            row["min"],
            row["max"],
        )
        for row in rows.get(ENTITY, [])
    }


async def _five_minutes(hass: HomeAssistant) -> dict[tuple[int, int], float]:
    """Return the five-minute means of the sensor by (hour, minute) of DAY."""
    await async_wait_recording_done(hass)
    rows = await hass.async_add_executor_job(
        statistics_during_period,
        hass,
        DAY,
        None,
        {ENTITY},
        "5minute",
        None,
        {"mean"},
    )
    periods = {}
    for row in rows.get(ENTITY, []):
        moment = datetime.fromtimestamp(row["start"], UTC)
        periods[moment.hour, moment.minute] = row["mean"]
    return periods


def _five_minute_means(
    start: datetime, end: datetime, mean: float
) -> dict[tuple[int, int], float]:
    """Return the same mean for each five-minute period from start to end."""
    periods = {}
    moment = start
    while moment < end:
        periods[moment.hour, moment.minute] = mean
        moment += timedelta(minutes=5)
    return periods


async def test_periods_compiled_before_a_backdated_value_are_recomputed(
    recorder_mock: Recorder, hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """The periods from 9:00 are recomputed once hour 12 is compiled; none is added."""
    freezer.move_to(_at(6, 10))
    report(hass, "30", _at(6))
    await setup_sensor(hass)
    await async_wait_recording_done(hass)

    freezer.move_to(_at(12, 20))
    await _compile(hass, _at(6), _at(12, 15))
    report(hass, "50", _at(9, 30))
    await hass.async_block_till_done()
    await _compile(hass, _at(12, 15), _at(13))

    hours = await _hours(hass)
    assert hours[9] == (30, 30, 30)
    assert hours[12] == (pytest.approx(45), 30, 50)
    compiled = _five_minute_means(_at(6), _at(12, 15), 30) | _five_minute_means(
        _at(12, 15), _at(13), 50
    )
    assert await _five_minutes(hass) == compiled

    freezer.move_to(_at(13, 14))
    async_fire_time_changed(hass, _at(13, 14))
    await hass.async_block_till_done()
    assert (await _hours(hass))[12] == (pytest.approx(45), 30, 50)
    assert await _five_minutes(hass) == compiled

    freezer.move_to(_at(13, 16))
    async_fire_time_changed(hass, _at(13, 16))
    await hass.async_block_till_done()

    assert await _hours(hass) == {
        6: (30, 30, 30),
        7: (30, 30, 30),
        8: (30, 30, 30),
        9: (40, 30, 50),
        10: (50, 50, 50),
        11: (50, 50, 50),
        12: (50, 50, 50),
    }
    assert await _five_minutes(hass) == _five_minute_means(
        _at(6), _at(9, 30), 30
    ) | _five_minute_means(_at(9, 30), _at(13), 50)


async def _report_late(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, captured: datetime
) -> None:
    """Record 30 from 6:00, then 50 captured at captured, which arrives at 8:20."""
    freezer.move_to(_at(6, 10))
    report(hass, "30", _at(6))
    await setup_sensor(hass)
    await async_wait_recording_done(hass)

    freezer.move_to(_at(8, 20))
    await _compile(hass, _at(6), _at(8, 15))
    report(hass, "50", captured)
    await hass.async_block_till_done()
    await _compile(hass, _at(8, 15), _at(9))


async def _correct(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """Run the correction scheduled for 9:15."""
    freezer.move_to(_at(9, 16))
    async_fire_time_changed(hass, _at(9, 16))
    await hass.async_block_till_done()


async def test_a_value_captured_on_the_hour_counts_from_there(
    recorder_mock: Recorder, hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """A row recorded exactly where the correction starts is part of it."""
    await _report_late(hass, freezer, _at(7))
    await _correct(hass, freezer)

    assert await _hours(hass) == {6: (30, 30, 30), 7: (50, 50, 50), 8: (50, 50, 50)}
    assert await _five_minutes(hass) == _five_minute_means(
        _at(6), _at(7), 30
    ) | _five_minute_means(_at(7), _at(9), 50)


async def test_a_pending_correction_is_stored_for_a_restart(
    recorder_mock: Recorder, hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """The restore data holds the hour a pending correction starts at."""
    freezer.move_to(_at(6, 2))
    report(hass, "30", _at(6, 1))
    await setup_sensor(hass)
    freezer.move_to(_at(9, 40))
    report(hass, "50", _at(7, 30))
    await hass.async_block_till_done()

    data = await async_mock_restore_state_shutdown_restart(hass)
    assert data.last_states[ENTITY].extra_data.as_dict() == {
        "native_value": 50,
        "captured": _at(7, 30).isoformat(),
        "written": _at(7, 30).timestamp(),
        "correct_statistics_from": _at(7).isoformat(),
        "options": None,
    }


async def test_a_restored_correction_runs_after_the_next_hour(
    recorder_mock: Recorder, hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """After a restart, the correction runs once the hour then running is compiled."""
    freezer.move_to(_at(9, 40))
    mock_restore_cache_with_extra_data(
        hass,
        (
            (
                State(ENTITY, "50"),
                {
                    "native_value": 50,
                    "captured": _at(7, 30).isoformat(),
                    "written": _at(7, 30).timestamp(),
                    "correct_statistics_from": _at(7).isoformat(),
                },
            ),
        ),
    )
    with patch(
        "custom_components.backdated_sensor.entity.async_correct_statistics",
        return_value={"5minute": 36, "hour": 3},
    ) as correct:
        await setup_sensor(hass)
        freezer.move_to(_at(10, 16))
        async_fire_time_changed(hass, _at(10, 16))
        await hass.async_block_till_done()

    correct.assert_awaited_once_with(hass, ENTITY, "%", _at(7), _at(10))


async def test_a_value_captured_in_the_running_period_needs_no_correction(
    recorder_mock: Recorder, hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Nothing is scheduled when no compiled period lies behind the new value."""
    freezer.move_to(_at(9, 44))
    report(hass, "30", _at(9, 41))
    await setup_sensor(hass)

    data = await async_mock_restore_state_shutdown_restart(hass)
    assert (
        data.last_states[ENTITY].extra_data.as_dict()["correct_statistics_from"] is None
    )


def test_period_statistics_weights_values_by_duration() -> None:
    """The mean follows the recorder's time-weighted average."""
    values = [(10.0, _at(5, 50)), (20.0, _at(6, 15)), (40.0, _at(6, 45))]
    assert period_statistics(values, _at(6), _at(7)) == {
        "start": _at(6),
        "mean": pytest.approx((10 * 15 + 20 * 30 + 40 * 15) / 60),
        "min": 10.0,
        "max": 40.0,
    }
    assert period_statistics(values, _at(7), _at(8)) == {
        "start": _at(7),
        "mean": 40.0,
        "min": 40.0,
        "max": 40.0,
    }
    assert period_statistics(values[1:], _at(6), _at(7)) == {
        "start": _at(6),
        "mean": pytest.approx((20 * 30 + 40 * 15) / 45),
        "min": 20.0,
        "max": 40.0,
    }
    assert period_statistics(values, _at(4), _at(5)) is None
    assert period_statistics(values, _at(6, 10), _at(6, 15)) == {
        "start": _at(6, 10),
        "mean": 10.0,
        "min": 10.0,
        "max": 10.0,
    }
    assert period_statistics(values, _at(6, 15), _at(6, 20)) == {
        "start": _at(6, 15),
        "mean": 20.0,
        "min": 20.0,
        "max": 20.0,
    }
