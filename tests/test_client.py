"""Tests for the vendored Modbus client, run against a fake S21.

These cover the socket lifecycle, which is where the "entities become
unavailable" bug lived. They are deliberately hardware-free so the regressions
stay covered in CI.
"""
from __future__ import annotations

import asyncio
import contextlib

import pytest
from pybls21.client import S21Client
from pybls21.exceptions import (
    ModbusCommunicationException,
    UnsupportedDeviceException,
)
from pybls21.models import HVACAction, HVACMode

from tests.fake_s21 import Fault

pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------------- polling
async def test_poll_decodes_the_register_map(client, fake_server):
    device = await client.poll()

    assert device.available is True
    assert device.manufacturer == "Blauberg"
    assert device.model == "S21"
    assert device.unique_id == f"S21_{fake_server.address[0]}_{fake_server.address[1]}"
    assert device.sw_version == "1.9 (2024-06-28)"

    # Temperatures are tenths of a degree on the wire.
    assert device.current_temperature == 23.2
    assert device.current_intake_temperature == 21.0
    assert device.current_outlet_temperature_in == 22.0
    assert device.current_outlet_temperature_out == 15.0
    assert device.target_temperature == 20
    assert device.current_humidity == 45

    assert device.max_fan_level == 3
    assert device.fan_modes == [1, 2, 3, 255]
    assert device.fan_mode == 2
    assert device.hvac_mode == HVACMode.HEAT
    assert device.hvac_action == HVACAction.HEATING

    assert device.supply_fan_speed == 2460
    assert device.extract_fan_speed == 2520
    assert device.filter_countdown == 90
    assert device.bypass_type == 1
    assert device.bypass_mode == 2
    assert device.timer_countdown == "00:00:00"
    assert device.alarm_state == 0
    assert device.alarm_codes == []


async def test_poll_reports_no_humidity_when_sensor_reads_zero(client, fake_server):
    fake_server.inputs[10] = 0
    device = await client.poll()
    assert device.current_humidity is None


async def test_poll_decodes_negative_temperatures(client, fake_server):
    fake_server.inputs[1] = 0x10000 - 55  # -5.5 C as a signed 16-bit value
    device = await client.poll()
    assert device.current_intake_temperature == -5.5


async def test_poll_reads_alarm_codes_only_when_an_alarm_is_active(client, fake_server):
    device = await client.poll()
    assert device.alarm_codes == []

    fake_server.inputs[38] = 2          # IR_ALARM
    fake_server.discrete[19 + 23] = True  # alarm code 23
    fake_server.discrete[19 + 4] = True   # alarm code 4
    device = await client.poll()
    assert device.alarm_state == 2
    assert device.alarm_codes == [4, 23]


async def test_poll_decodes_the_timer_countdown(client, fake_server):
    fake_server.coils[1] = True                    # CL_TIMER
    fake_server.inputs[25] = (34 << 8) | 56        # 34 minutes, 56 seconds
    fake_server.inputs[26] = 12                    # 12 hours
    device = await client.poll()
    assert device.is_timer is True
    assert device.timer_countdown == "12:34:56"


@pytest.mark.parametrize(
    ("power", "operation_mode", "expected_mode", "expected_action"),
    [
        (False, 1, HVACMode.OFF, HVACAction.OFF),
        (True, 0, HVACMode.FAN_ONLY, HVACAction.FAN),
        (True, 1, HVACMode.HEAT, HVACAction.HEATING),
        (True, 2, HVACMode.COOL, HVACAction.COOLING),
        (True, 3, HVACMode.AUTO, HVACAction.HEATING),
    ],
)
async def test_poll_maps_operating_modes(
    client, fake_server, power, operation_mode, expected_mode, expected_action
):
    fake_server.coils[0] = power
    fake_server.holding[43] = operation_mode
    device = await client.poll()
    assert device.hvac_mode == expected_mode
    assert device.hvac_action == expected_action


async def test_unsupported_device_is_rejected_without_retrying(client, fake_server):
    fake_server.fault = Fault.WRONG_DEVICE_TYPE
    fake_server.reset_counters()

    with pytest.raises(UnsupportedDeviceException):
        await client.poll()

    assert fake_server.connections_accepted == 1, "must not retry a wrong device type"


# ------------------------------------------------------------- socket lifecycle
async def test_socket_is_released_after_every_operation(client, fake_server):
    for _ in range(5):
        await client.poll()
        await fake_server.wait_idle()

    assert fake_server.connections_accepted == 5
    assert fake_server.peak_concurrent == 1


async def test_many_polls_do_not_accumulate_connections(client, fake_server):
    for _ in range(30):
        await client.poll()
    await fake_server.wait_idle()
    assert fake_server.peak_concurrent == 1


async def test_concurrent_operations_are_serialised(client, fake_server):
    results = await asyncio.gather(*(client.poll() for _ in range(8)))

    assert len(results) == 8
    assert all(device is not None for device in results)
    # The unit only tolerates one connection, so the lock must prevent overlap.
    assert fake_server.peak_concurrent == 1
    assert fake_server.connections_refused == 0


async def test_cancelling_a_poll_does_not_orphan_the_connection(client, fake_server):
    """Regression: a cancelled connect used to leak an ESTABLISHED socket.

    The client wrapped ``connect()`` in ``asyncio.wait_for``. Cancelling the
    handshake left a socket that ``close()`` could not reclaim, and because the
    unit only accepts one connection every later poll failed for good.
    """
    for delay in (0, 0.0005, 0.001, 0.005):
        task = asyncio.create_task(client.poll())
        if delay:
            await asyncio.sleep(delay)
        task.cancel()
        with pytest.raises((asyncio.CancelledError, ModbusCommunicationException)):
            await task
        await fake_server.wait_idle()

    # Crucially, the client must still work.
    assert await client.poll() is not None


async def test_client_recovers_after_the_slot_is_freed(client, fake_server):
    await client.poll()

    fake_server.fault = Fault.REFUSE_CONNECTIONS
    with pytest.raises(ModbusCommunicationException):
        await client.poll()
    assert client.device is not None and client.device.available is False

    fake_server.fault = Fault.NONE
    device = await client.poll()
    assert device.available is True


async def test_a_foreign_connection_blocks_us_but_leaks_nothing(client, fake_server):
    """The unit's single slot taken by someone else, e.g. the vendor app."""
    host, port = fake_server.address
    reader, writer = await asyncio.open_connection(host, port)
    try:
        assert fake_server.active_connections == 1

        with pytest.raises(ModbusCommunicationException):
            await client.poll()
        assert fake_server.connections_refused >= 1
        assert fake_server.active_connections == 1, "only the foreign one remains"
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        del reader

    await asyncio.sleep(0.05)
    assert await client.poll() is not None


async def test_no_asyncio_tasks_are_left_behind(client, fake_server, pending_tasks):
    before = len(pending_tasks())
    for _ in range(5):
        await client.poll()
    fake_server.fault = Fault.REFUSE_CONNECTIONS
    with pytest.raises(ModbusCommunicationException):
        await client.poll()
    fake_server.fault = Fault.NONE
    await client.poll()
    await asyncio.sleep(0.1)
    assert len(pending_tasks()) <= before


# ------------------------------------------------------------------ close/reuse
async def test_close_is_idempotent(client):
    await client.poll()
    await client.close()
    await client.close()
    client.close_nowait()


async def test_a_closed_client_refuses_further_work(client):
    await client.poll()
    await client.close()

    with pytest.raises(ModbusCommunicationException, match="closed"):
        await client.poll()


async def test_close_nowait_releases_the_slot_immediately(fake_server):
    """This is what the EVENT_HOMEASSISTANT_STOP handler calls."""
    host, port = fake_server.address

    first = S21Client(host, port)
    await first.poll()
    first.close_nowait()
    await fake_server.wait_idle()

    # A brand new client - i.e. Home Assistant after a restart - must connect
    # straight away rather than being locked out.
    second = S21Client(host, port)
    try:
        assert await second.poll() is not None
    finally:
        await second.close()


async def test_repeated_restart_cycles_always_reconnect(fake_server):
    """Ten restart cycles in a row must all get their data.

    Reconnecting the instant the previous socket closed can race the unit
    freeing its only slot, so some attempts are refused. The retry layer is
    expected to absorb that, which is exactly what happens on real hardware
    (a restart there costs ~0.6 s: one refusal plus the first backoff).
    """
    host, port = fake_server.address
    for _ in range(10):
        instance = S21Client(host, port)
        assert await instance.poll() is not None
        instance.close_nowait()

    await fake_server.wait_idle()
    assert fake_server.peak_concurrent == 1, "connections must never overlap"


async def test_restart_cycles_are_refusal_free_once_the_slot_is_free(fake_server):
    """With the slot demonstrably free, a restart must connect first time."""
    host, port = fake_server.address
    for _ in range(10):
        instance = S21Client(host, port)
        assert await instance.poll() is not None
        instance.close_nowait()
        await fake_server.wait_idle()

    assert fake_server.connections_refused == 0
    assert fake_server.connections_accepted == 10


# ---------------------------------------------------------------------- retries
async def test_transient_failures_are_retried(client, fake_server):
    """The first attempt fails, a later one succeeds."""
    fake_server.fault = Fault.REFUSE_CONNECTIONS
    fake_server.reset_counters()

    async def clear_fault_shortly() -> None:
        await asyncio.sleep(0.35)
        fake_server.fault = Fault.NONE

    task = asyncio.create_task(clear_fault_shortly())
    device = await client.poll()
    await task

    assert device is not None
    assert fake_server.connections_refused >= 1, "should have failed at least once"


async def test_failures_are_bounded_and_reported(client, fake_server):
    fake_server.fault = Fault.REFUSE_CONNECTIONS

    loop = asyncio.get_running_loop()
    started = loop.time()
    with pytest.raises(ModbusCommunicationException, match="after 3 attempts"):
        await client.poll()
    elapsed = loop.time() - started

    assert elapsed < 20, "must give up well inside the operation deadline"


async def test_modbus_exception_responses_are_surfaced(client, fake_server):
    fake_server.fault = Fault.EXCEPTION_RESPONSE
    with pytest.raises(ModbusCommunicationException):
        await client.poll()
    await fake_server.wait_idle()


async def test_a_connection_dropped_mid_request_is_handled(client, fake_server):
    fake_server.fault = Fault.DROP_MID_REQUEST
    with pytest.raises(ModbusCommunicationException):
        await client.poll()
    await fake_server.wait_idle()

    fake_server.fault = Fault.NONE
    assert await client.poll() is not None


async def test_connecting_to_a_closed_port_fails_cleanly(closed_port, pending_tasks):
    host, port = closed_port
    instance = S21Client(host, port)
    try:
        with pytest.raises(ModbusCommunicationException):
            await instance.poll()
    finally:
        await instance.close()
    await asyncio.sleep(0.05)
    assert not pending_tasks()


# -------------------------------------------------------------------- writes
async def test_set_temperature_writes_the_holding_register(client, fake_server):
    await client.poll()
    await client.set_temperature(24)
    assert ("register", 44, 24) in fake_server.writes
    assert (await client.poll()).target_temperature == 24


async def test_set_fan_mode_writes_the_speed_register(client, fake_server):
    await client.poll()
    await client.set_fan_mode(3)
    assert ("register", 2, 3) in fake_server.writes


async def test_set_fan_mode_defaults_the_limit_to_the_device(client, fake_server):
    """A unit with more than three speeds must accept its higher levels."""
    fake_server.holding[1] = 8  # HR_MaxSPEED_MODE
    await client.poll()

    await client.set_fan_mode(7)
    assert ("register", 2, 7) in fake_server.writes

    with pytest.raises(ValueError):
        await client.set_fan_mode(9)


async def test_set_bypass_mode_writes_the_bypass_register(client, fake_server):
    await client.poll()
    await client.set_bypass_mode(1)
    assert ("register", 74, 1) in fake_server.writes


async def test_power_and_mode_writes(client, fake_server):
    await client.poll()

    await client.turn_off()
    assert ("coil", 0, 0) in fake_server.writes

    await client.set_hvac_mode(HVACMode.COOL)
    assert ("coil", 0, 1) in fake_server.writes
    assert ("register", 43, 2) in fake_server.writes


async def test_boost_timer_and_schedule_coils(client, fake_server):
    await client.poll()

    await client.set_boost_on()
    assert ("coil", 13, 1) in fake_server.writes
    await client.set_timer_on()
    assert ("coil", 1, 1) in fake_server.writes
    await client.set_scheduler_mode_on()
    assert ("coil", 2, 1) in fake_server.writes

    device = await client.poll()
    assert device.is_boosting is True
    assert device.is_timer is True
    assert device.is_schedule_mode is True


async def test_reset_coils(client, fake_server):
    await client.poll()
    await client.reset_filter_change_timer()
    assert ("coil", 17, 1) in fake_server.writes
    await client.reset_alarm()
    assert ("coil", 18, 1) in fake_server.writes


# ------------------------------------------------------------------ validation
@pytest.mark.parametrize("value", [14, 31, -5, 20.5, "20", None])
async def test_invalid_temperatures_are_rejected(client, fake_server, value):
    fake_server.reset_counters()
    with pytest.raises(ValueError):
        await client.set_temperature(value)
    assert fake_server.connections_accepted == 0, "must not touch the device"


@pytest.mark.parametrize("value", [0, 4, 99, -1, "high", None])
async def test_invalid_fan_modes_are_rejected(client, fake_server, value):
    fake_server.reset_counters()
    with pytest.raises(ValueError):
        await client.set_fan_mode(value, 3)
    assert fake_server.connections_accepted == 0


@pytest.mark.parametrize("value", [3, -1, 99, "auto", None])
async def test_invalid_bypass_modes_are_rejected(client, fake_server, value):
    fake_server.reset_counters()
    with pytest.raises(ValueError):
        await client.set_bypass_mode(value)
    assert fake_server.connections_accepted == 0


@pytest.mark.parametrize("value", [-1, 101, 500, "50", None])
async def test_invalid_manual_fan_speeds_are_rejected(client, fake_server, value):
    fake_server.reset_counters()
    with pytest.raises(ValueError):
        await client.set_manual_fan_speed_percent(value)
    assert fake_server.connections_accepted == 0


@pytest.mark.parametrize("value", [1, 255])
async def test_valid_fan_modes_are_accepted(client, value):
    await client.poll()
    await client.set_fan_mode(value, 3)
