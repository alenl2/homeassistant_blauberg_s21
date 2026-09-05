"""Tests against real Blauberg S21 hardware.

Skipped unless ``BLAUBERG_S21_HOST`` names a unit, so no address is ever stored
in the repository:

    # PowerShell
    $env:BLAUBERG_S21_HOST = "<your-unit>"; pytest tests/test_live.py -v

    # bash
    BLAUBERG_S21_HOST=<your-unit> pytest tests/test_live.py -v

These are read-only by default. Write coverage is opt-in separately with
``BLAUBERG_S21_ALLOW_WRITES=1``; even then every write puts the value that was
just read straight back, so the unit's configuration is never changed.

Destructive commands are never exercised here. In particular
``reset_filter_change_timer`` would wipe the real filter countdown, and powering
the unit off would interrupt ventilation, so both are covered by the fake-server
tests instead.
"""
from __future__ import annotations

import asyncio
import contextlib
import os

import pytest
from pybls21.client import S21Client
from pybls21.exceptions import ModbusCommunicationException

pytestmark = [pytest.mark.asyncio, pytest.mark.live]

ALLOW_WRITES = os.environ.get("BLAUBERG_S21_ALLOW_WRITES") == "1"

requires_writes = pytest.mark.skipif(
    not ALLOW_WRITES,
    reason="set BLAUBERG_S21_ALLOW_WRITES=1 to exercise the write paths",
)


@pytest.fixture
async def live_client(live_target):
    host, port = live_target
    instance = S21Client(host, port)
    try:
        yield instance
    finally:
        await instance.close()


async def wait_for_free_slot(host: str, port: int, timeout: float = 60.0) -> None:
    """Wait until the unit's single connection slot is available."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        try:
            _reader, writer = await asyncio.open_connection(host, port)
        except OSError:
            if loop.time() > deadline:
                pytest.skip("the unit's connection slot never became free")
            await asyncio.sleep(2.0)
            continue
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        await asyncio.sleep(0.3)
        return


# ------------------------------------------------------------------- read only
async def test_poll_returns_plausible_readings(live_client, live_target):
    host, port = live_target
    device = await live_client.poll()

    assert device.available is True
    assert device.manufacturer == "Blauberg"
    assert device.model == "S21"
    assert device.unique_id == f"S21_{host}_{port}"
    assert device.sw_version and device.sw_version != "unknown"

    assert 1 <= device.max_fan_level <= 8
    assert device.fan_modes == [i + 1 for i in range(device.max_fan_level)] + [255]
    assert 15 <= device.target_temperature <= 30
    assert -40 <= device.current_temperature <= 60
    assert device.hvac_mode is not None
    assert device.hvac_action is not None
    assert isinstance(device.alarm_codes, list)
    assert device.bypass_mode in (0, 1, 2)
    assert device.timer_countdown.count(":") == 2


async def test_poll_reports_all_four_air_stream_temperatures(live_client):
    """The four readings the temperature sensors are built from."""
    device = await live_client.poll()

    readings = {
        "supply outdoor": device.current_intake_temperature,
        "supply": device.current_intake_temperature_out,
        "extract": device.current_outlet_temperature_in,
        "extract outlet": device.current_outlet_temperature_out,
    }
    for label, value in readings.items():
        assert value is not None, f"{label} temperature missing"
        assert -40 <= value <= 60, f"{label} temperature implausible: {value}"

    # The climate entity mirrors the supply temperature.
    assert device.current_temperature == device.current_intake_temperature_out


async def test_poll_reports_the_manual_fan_speed_percentage(live_client):
    """The value the manual fan speed slider is built from."""
    device = await live_client.poll()
    assert device.manual_fan_speed_percent is not None
    assert 0 <= device.manual_fan_speed_percent <= 100


async def test_poll_is_fast(live_client):
    await live_client.poll()  # warm up

    loop = asyncio.get_running_loop()
    durations = []
    for _ in range(5):
        started = loop.time()
        await live_client.poll()
        durations.append(loop.time() - started)

    assert max(durations) < 3.0, f"slowest poll took {max(durations):.2f}s"


async def test_repeated_polls_are_stable(live_client):
    for _ in range(20):
        assert (await live_client.poll()).available is True


async def test_concurrent_polls_are_serialised(live_client):
    results = await asyncio.gather(*(live_client.poll() for _ in range(5)))
    assert all(device.available for device in results)


async def test_alarm_codes_are_read_when_an_alarm_is_active(live_client):
    """Exercises the discrete-input read, but only if the unit has an alarm."""
    device = await live_client.poll()
    if not device.alarm_state:
        pytest.skip("the unit has no active alarm or warning")
    assert device.alarm_codes, "an alarm state should decode to at least one code"
    assert all(0 <= code <= 52 for code in device.alarm_codes)


# -------------------------------------------------------- socket lifecycle
async def test_restart_cycle_reconnects_promptly(live_target):
    """The behaviour the EVENT_HOMEASSISTANT_STOP hook exists to guarantee."""
    host, port = live_target
    await wait_for_free_slot(host, port)

    loop = asyncio.get_running_loop()
    worst = 0.0
    for _ in range(5):
        old = S21Client(host, port)
        await old.poll()
        old.close_nowait()          # what the stop hook does

        new = S21Client(host, port)
        started = loop.time()
        try:
            assert await new.poll() is not None
            worst = max(worst, loop.time() - started)
        finally:
            await new.close()

    assert worst < 10.0, f"slowest reconnect after a restart took {worst:.2f}s"


async def test_cancelling_a_poll_leaves_the_unit_usable(live_client):
    """Regression: a cancelled connect used to lock us out of the unit."""
    for delay in (0.001, 0.01, 0.05):
        task = asyncio.create_task(live_client.poll())
        await asyncio.sleep(delay)
        task.cancel()
        with pytest.raises((asyncio.CancelledError, ModbusCommunicationException)):
            await task
        await asyncio.sleep(0.3)

    assert await live_client.poll() is not None


async def test_a_foreign_client_blocks_us_and_we_recover(live_client, live_target):
    """The unit only has one slot; losing it must not be permanent."""
    host, port = live_target
    await live_client.poll()
    await wait_for_free_slot(host, port)

    _reader, writer = await asyncio.open_connection(host, port)
    try:
        with pytest.raises(ModbusCommunicationException):
            await live_client.poll()
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()

    await asyncio.sleep(1.0)
    recovered = False
    for _ in range(15):
        try:
            await live_client.poll()
            recovered = True
            break
        except ModbusCommunicationException:
            await asyncio.sleep(2.0)
    assert recovered, "the client never recovered after the slot was released"


async def test_a_closed_client_refuses_further_work(live_client):
    await live_client.poll()
    await live_client.close()
    with pytest.raises(ModbusCommunicationException, match="closed"):
        await live_client.poll()


@pytest.mark.slow
async def test_soak(live_client):
    """Poll for a while and confirm nothing degrades."""
    duration = float(os.environ.get("BLAUBERG_S21_SOAK_SECONDS", "60"))
    loop = asyncio.get_running_loop()
    deadline = loop.time() + duration

    polls = 0
    worst = 0.0
    while loop.time() < deadline:
        started = loop.time()
        assert (await live_client.poll()).available is True
        worst = max(worst, loop.time() - started)
        polls += 1
        await asyncio.sleep(1.0)

    assert polls > 5, f"only managed {polls} polls in {duration}s"
    assert worst < 3.0, f"slowest poll took {worst:.2f}s"


# ------------------------------------------------------------------- writes
@requires_writes
async def test_writing_the_current_temperature_back_is_accepted(live_client):
    device = await live_client.poll()
    current = int(device.target_temperature)

    await live_client.set_temperature(current)

    assert int((await live_client.poll()).target_temperature) == current


@requires_writes
async def test_writing_the_current_fan_level_back_is_accepted(live_client):
    device = await live_client.poll()
    current = device.fan_level_manual_mode
    if current not in [i + 1 for i in range(device.max_fan_level)] + [255]:
        pytest.skip(f"current fan level {current} is not a settable value")

    await live_client.set_fan_mode(current)

    assert (await live_client.poll()).fan_level_manual_mode == current


@requires_writes
async def test_writing_the_current_bypass_mode_back_is_accepted(live_client):
    device = await live_client.poll()
    current = device.bypass_mode
    if current not in (0, 1, 2):
        pytest.skip(f"current bypass mode {current} is not a settable value")

    await live_client.set_bypass_mode(current)

    assert (await live_client.poll()).bypass_mode == current


@requires_writes
async def test_writing_the_current_boost_state_back_is_accepted(live_client):
    """Exercises the coil write path without changing anything."""
    device = await live_client.poll()
    if device.is_boosting:
        await live_client.set_boost_on()
    else:
        await live_client.set_boost_off()

    assert (await live_client.poll()).is_boosting == device.is_boosting


@requires_writes
async def test_writing_the_current_manual_fan_speed_back_is_accepted(live_client):
    """The register behind the manual fan speed slider."""
    device = await live_client.poll()
    current = device.manual_fan_speed_percent
    assert current is not None

    await live_client.set_manual_fan_speed_percent(int(current))

    assert (await live_client.poll()).manual_fan_speed_percent == current


async def test_validation_rejects_bad_values_without_contacting_the_unit(live_client):
    """Safe even without write permission: nothing reaches the device."""
    await live_client.poll()

    for coroutine in (
        live_client.set_temperature(5),
        live_client.set_temperature(99),
        live_client.set_fan_mode(0, 3),
        live_client.set_fan_mode(99, 3),
        live_client.set_bypass_mode(7),
        live_client.set_manual_fan_speed_percent(500),
    ):
        with pytest.raises(ValueError):
            await coroutine
