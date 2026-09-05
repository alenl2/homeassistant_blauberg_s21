import asyncio
import logging
import time
from typing import Any, Awaitable, Callable, List, Optional

from pymodbus.client import AsyncModbusTcpClient

from .constants import *
from .exceptions import (
    ModbusCommunicationException,
    UnsupportedDeviceException,
)
from .models import (
    TEMP_CELSIUS,
    ClimateDevice,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)

_LOGGER = logging.getLogger(__name__)

# Connection parameters, measured against a real Blauberg S21 (fw 0.43):
#
#   * connect + full register sweep takes ~115 ms
#   * the unit accepts exactly ONE TCP connection; any further connection is
#     answered with a TCP reset
#   * back-to-back connect/close cycles need no settle delay at all
#   * a socket that is closed cleanly frees the unit within ~0.1-0.6 s
#   * a socket that is NOT closed cleanly (peer still holding it open) keeps the
#     unit locked out for a very long time - observed from ~1 minute up to
#     indefinitely. This is why every code path below closes the client, and why
#     the integration also closes it on EVENT_HOMEASSISTANT_STOP.
#
# Because the unit is single-connection, we hold the socket only for the
# duration of a single operation and close it immediately afterwards. That
# keeps the unit reachable for other clients (e.g. the vendor app) and avoids
# the "long connections break over time" behaviour of the firmware.
_REQUEST_TIMEOUT = 4.0     # per-request budget, enforced internally by pymodbus
_PYMODBUS_RETRIES = 1      # we do our own retrying, don't let pymodbus multiply it
_MAX_ATTEMPTS = 3          # attempts per operation
_RETRY_BACKOFF = (0.5, 1.5)  # sleep before attempt 2 and 3
_OPERATION_DEADLINE = 20.0   # cooperative overall budget for one operation

# pymodbus converts an externally cancelled request into a plain
# ModbusIOException instead of re-raising CancelledError. We detect that so we
# can restore proper cooperative cancellation for Home Assistant shutdown.
_CANCELLED_MARKER = "cancelled outside library"


def _parse_firmware_version(firmware_info: List[int]) -> str:
    if not isinstance(firmware_info, list) or len(firmware_info) < 3:
        return "unknown"

    try:
        major, minor = firmware_info[0].to_bytes(2, "big")
        day, month = firmware_info[1].to_bytes(2, "big")
        year: int = firmware_info[2]
        return f"{major}.{minor} ({year}-{month:02d}-{day:02d})"

    except (ValueError, OverflowError):
        return "unknown"

def _to_signed_16bit(value: int) -> int:
    return value - 0x10000 if value > 0x7FFF else value


class S21Client:
    def __init__(self, host: str, port: int = 502):
        self.host = host
        self.port = port
        self._client: Optional[AsyncModbusTcpClient] = None
        self.device: Optional[ClimateDevice] = None
        self.lock = asyncio.Lock()
        self._closed = False

    def _create_client(self) -> AsyncModbusTcpClient:
        """Create a fresh Modbus TCP client with proper timeouts."""
        return AsyncModbusTcpClient(
            host=self.host,
            port=self.port,
            timeout=_REQUEST_TIMEOUT,
            retries=_PYMODBUS_RETRIES,
        )

    async def close(self) -> None:
        """Cleanly close the underlying Modbus connection.

        Must be called when Home Assistant shuts down. The unit only accepts a
        single connection, and it keeps that slot reserved for as long as the
        peer holds the socket open, so failing to close it locks the integration
        out of the device after a restart.
        """
        async with self.lock:
            self._closed = True
            self._close_client()

    def _close_client(self) -> None:
        """Close and drop the client. Safe to call repeatedly.

        pymodbus' close() is synchronous, so this does not need to be a
        coroutine; keeping it sync guarantees it also works from a callback
        such as an EVENT_HOMEASSISTANT_STOP handler.
        """
        client, self._client = self._client, None
        if client is None:
            return
        try:
            client.close()
        except Exception:  # pragma: no cover - defensive
            _LOGGER.debug("Ignoring error while closing Modbus client", exc_info=True)

    def close_nowait(self) -> None:
        """Close the socket without waiting for the lock.

        Used on Home Assistant shutdown, where we must not await anything and
        cannot risk blocking behind an in-flight poll.
        """
        self._closed = True
        self._close_client()

    # -----------------------------------------------------------
    # Functions to get device information
    async def poll(self) -> ClimateDevice:
        return await self._do_with_connection(self._poll)

    async def _poll(self) -> ClimateDevice:
        _LOGGER.debug("Polling device at %s:%s", self.host, self.port)

        if (await self._read_input_registers(IR_DeviceTYPE, count=1))[0] != 1:
            raise UnsupportedDeviceException("Unsupported device (IR_DeviceTYPE != 1)")

        coils = await self._read_coils(0, count=4)
        holding_registers = await self._read_holding_registers(0, count=75)
        input_registers = await self._read_input_registers(0, count=39)

        is_on: bool = coils[CL_POWER]
        is_boosting: bool = coils[CL_Boost_MODE]
        set_temperature: int = holding_registers[HR_SetTEMP]
        current_humidity: int = input_registers[IR_CurRH_Int]
        filter_state: int = input_registers[IR_StateFILTER]
        alarm_state: int = input_registers[IR_ALARM]

        alarm_codes: list[int] = []
        if alarm_state > 0:
            alarm_codes = await self._read_alarm_codes()

        max_fan_level: int = holding_registers[HR_MaxSPEED_MODE]
        current_fan_level: int = holding_registers[HR_SPEED_MODE]  # 255 - manual
        temp_before_heating_x10: int = _to_signed_16bit(
            input_registers[IR_CurTEMP_SuAirIn]
        )
        temp_after_heating_x10: int = _to_signed_16bit(
            input_registers[IR_CurTEMP_SuAirOut]
        )
        supply_fan_speed: int = input_registers[IR_SuRPM]
        extract_fan_speed: int = input_registers[IR_ExRPM]
        firmware_info: List[int] = input_registers[
            IR_VerMAIN_FMW_start : IR_VerMAIN_FMW_end + 1
        ]
        operation_mode: int = holding_registers[HR_OPERATION_MODE]
        manual_fan_speed_percent: int = holding_registers[HR_ManualSPEED]

        is_timer: bool = coils[CL_TIMER]
        main_timer_sec: int = input_registers[IR_CurTIMER_TIME] & 0xFF
        main_timer_min: int = ( input_registers[IR_CurTIMER_TIME] >> 8 ) & 0xFF
        main_timer_hrs: int = input_registers[IR_CurTIMER_TIME_HRS] & 0xFF

        is_schedule: bool = coils[CL_WEEK]
        current_schedule_mode_speed: int = input_registers[IR_CurWeekSpeed]

        bypass_type: int = holding_registers[HR_BYPASS_ROTOR_TYPE]
        bypass_mode: int = holding_registers[HR_BYPASS_ROTOR_MODE]

        temp_used_air_incoming_x10: int = _to_signed_16bit(
            input_registers[IR_CurTEMP_ExAirIn]
        )
        temp_used_air_outgoing_x10: int = _to_signed_16bit(
            input_registers[IR_CurTEMP_ExAirOut]
        )
        filter_countdown: int = input_registers[IR_CurFILTER_TIMER]
        pressure_air_incoming: int = input_registers[IR_CurSuPRESS]
        pressure_air_outgoing: int = input_registers[IR_CurExPRESS]

        self.device = ClimateDevice(
            available=True,
            name="Blauberg S21",
            unique_id=f"S21_{self.host}_{self.port}",
            temperature_unit=TEMP_CELSIUS,
            precision=1,
            current_temperature=temp_after_heating_x10 / 10,
            target_temperature=set_temperature,
            target_temperature_step=1,
            min_temp=15,
            max_temp=30,
            current_humidity=None if current_humidity == 0 else current_humidity,
            hvac_mode=
                HVACMode.OFF if not is_on
                else HVACMode.FAN_ONLY if operation_mode == 0
                else HVACMode.HEAT if operation_mode == 1
                else HVACMode.COOL if operation_mode == 2
                else HVACMode.AUTO,
            hvac_action=
                HVACAction.OFF if not is_on
                else HVACAction.FAN if operation_mode == 0
                else HVACAction.HEATING if operation_mode == 1
                else HVACAction.COOLING if operation_mode == 2
                else HVACAction.HEATING if temp_before_heating_x10 < temp_after_heating_x10
                else HVACAction.COOLING if temp_before_heating_x10 > temp_after_heating_x10
                else HVACAction.IDLE,
            hvac_modes=[
                HVACMode.OFF,
                HVACMode.HEAT,
                HVACMode.COOL,
                HVACMode.AUTO,
                HVACMode.FAN_ONLY,
            ],
            fan_mode=
                max_fan_level if is_boosting
                else max_fan_level if is_timer
                else current_schedule_mode_speed if is_schedule
                else current_fan_level,
            fan_modes=[x + 1 for x in range(max_fan_level)] + [255],
            supported_features=ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.FAN_MODE,
            manufacturer="Blauberg",
            model="S21",
            sw_version=_parse_firmware_version(firmware_info),
            is_boosting=is_boosting,
            current_intake_temperature=temp_before_heating_x10 / 10,
            manual_fan_speed_percent=manual_fan_speed_percent,
            max_fan_level=max_fan_level,
            filter_state=filter_state,
            alarm_state=alarm_state,
            supply_fan_speed=supply_fan_speed,
            extract_fan_speed=extract_fan_speed,
            alarm_codes=alarm_codes,
            current_intake_temperature_out=temp_after_heating_x10 / 10,
            current_outlet_temperature_in=temp_used_air_incoming_x10 / 10,
            current_outlet_temperature_out=temp_used_air_outgoing_x10 / 10,
            filter_countdown=filter_countdown,
            is_timer=is_timer,
            timer_countdown = f"{main_timer_hrs:02d}:{main_timer_min:02d}:{main_timer_sec:02d}",
            is_schedule_mode=is_schedule,
            fan_level_schedule_mode=current_schedule_mode_speed,
            fan_level_manual_mode=current_fan_level,
            bypass_type=bypass_type,
            bypass_mode=bypass_mode,
            pressure_air_incoming=pressure_air_incoming,
            pressure_air_outgoing=pressure_air_outgoing,
        )

        _LOGGER.debug("Poll successful: mode=%s, fan=%s, temp=%s, alarm=%s",
                  self.device.hvac_mode, self.device.fan_mode, self.device.current_temperature, self.device.alarm_state)

        return self.device

    # -----------------------------------------------------------
    # Functions to change individual settings
    async def set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        await self._do_with_connection(lambda: self._set_hvac_mode(hvac_mode))

    async def _set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            await self._set_turn_off()
        elif hvac_mode == HVACMode.FAN_ONLY:
            await self._set_turn_on()
            await self._write_register(HR_OPERATION_MODE, 0)
        elif hvac_mode == HVACMode.HEAT:
            await self._set_turn_on()
            await self._write_register(HR_OPERATION_MODE, 1)
        elif hvac_mode == HVACMode.COOL:
            await self._set_turn_on()
            await self._write_register(HR_OPERATION_MODE, 2)
        else:
            await self._set_turn_on()
            await self._write_register(HR_OPERATION_MODE, 3)

    async def set_fan_mode(self, mode: int, max_fan_level: Optional[int] = None) -> None:
        if max_fan_level is None:
            max_fan_level = self.device.max_fan_level if self.device else 3
        self._validate_fan_mode(mode, max_fan_level)
        await self._do_with_connection(lambda: self._set_fan_mode(mode))

    async def _set_fan_mode(self, mode: int) -> None:
        await self._write_register(HR_SPEED_MODE, mode)

    @staticmethod
    def _validate_fan_mode(mode: int, max_fan_level: int) -> None:
        valid = set(range(1, max_fan_level+1)) | {255}
        if not isinstance(mode, int) or mode not in valid:
            raise ValueError(f"Fan mode must be one of: {valid}; got: {mode}")

    async def set_manual_fan_speed_percent(self, speed_percent: int) -> None:
        self._validate_manual_fan_speed_percent(speed_percent)
        await self._do_with_connection(
            lambda: self._set_manual_fan_speed_percent(speed_percent)
        )

    async def _set_manual_fan_speed_percent(self, speed_percent: int) -> None:
        await self._write_register(HR_ManualSPEED, speed_percent)

    @staticmethod
    def _validate_manual_fan_speed_percent(speed_percent: int) -> None:
        if not isinstance(speed_percent, int) or not 0 <= speed_percent <= 100:
            raise ValueError(f"Manual fan speed percent must be between 0 and 100; got: {speed_percent}")

    async def set_temperature(self, temp_celsius: int) -> None:
        self._validate_temperature(temp_celsius)
        await self._do_with_connection(lambda: self._set_temperature(temp_celsius))

    async def _set_temperature(self, temp_celsius: int) -> None:
        await self._write_register(HR_SetTEMP, temp_celsius)

    @staticmethod
    def _validate_temperature(temp_celsius: int) -> None:
        if not isinstance(temp_celsius, int) or not 15 <= temp_celsius <= 30:
            raise ValueError(f"Temperature must be between 15 and 30 °C; got: {temp_celsius}")

    async def reset_filter_change_timer(self) -> None:
        await self._do_with_connection(self._reset_filter_change_timer)

    async def _reset_filter_change_timer(self) -> None:
        await self._write_coil(CL_RESET_FILTER_TIMER, True)

    async def reset_alarm(self) -> None:
        await self._do_with_connection(self._reset_alarm)

    async def _reset_alarm(self) -> None:
        await self._write_coil(CL_RESET_ALARM, True)

    async def turn_on(self) -> None:
        await self._do_with_connection(self._set_turn_on)

    async def _set_turn_on(self) -> None:
        await self._write_coil(CL_POWER, True)

    async def turn_off(self) -> None:
        await self._do_with_connection(self._set_turn_off)

    async def _set_turn_off(self) -> None:
        await self._write_coil(CL_POWER, False)

    async def set_boost_on(self) -> None:
        await self._do_with_connection(self._set_boost_on)

    async def _set_boost_on(self) -> None:
        await self._write_coil(CL_BoostSWITCH_CTRL, True)

    async def set_boost_off(self) -> None:
        await self._do_with_connection(self._set_boost_off)

    async def _set_boost_off(self) -> None:
        await self._write_coil(CL_BoostSWITCH_CTRL, False)

    async def set_timer_on(self) -> None:
        await self._do_with_connection(self._set_timer_on)

    async def _set_timer_on(self) -> None:
        await self._write_coil(CL_TIMER, True)

    async def set_timer_off(self) -> None:
        await self._do_with_connection(self._set_timer_off)

    async def _set_timer_off(self) -> None:
        await self._write_coil(CL_TIMER, False)

    async def set_scheduler_mode_on(self) -> None:
        await self._do_with_connection(self._set_scheduler_mode_on)

    async def _set_scheduler_mode_on(self) -> None:
        await self._write_coil(CL_WEEK, True)

    async def set_scheduler_mode_off(self) -> None:
        await self._do_with_connection(self._set_scheduler_mode_off)

    async def _set_scheduler_mode_off(self) -> None:
        await self._write_coil(CL_WEEK, False)

    async def set_bypass_mode(self, mode: int) -> None:
        self._validate_bypass_mode(mode)
        await self._do_with_connection(lambda: self._set_bypass_mode(mode))

    async def _set_bypass_mode(self, mode: int) -> None:
        await self._write_register(HR_BYPASS_ROTOR_MODE, mode)

    @staticmethod
    def _validate_bypass_mode(mode: int) -> None:
        if not isinstance(mode, int) or mode not in (0, 1, 2):
            raise ValueError(f"Bypass mode must be 0 (close/start), 1 (open/stop), or 2 (auto); got: {mode}")

    async def _read_alarm_codes(self) -> list[int]:
        """Read active alarm codes from Discrete Inputs 19-71."""
        DI_ALARM_START = 19
        DI_ALARM_COUNT = 53  # codes 0-52
        bits = await self._read_discrete_inputs(DI_ALARM_START, DI_ALARM_COUNT)
        return [i for i, active in enumerate(bits) if active]

    # -----------------------------------------------------------
    # Connection management with retry logic
    @staticmethod
    def _reraise_if_cancelled(exc: BaseException) -> None:
        """Restore cooperative cancellation swallowed by pymodbus.

        pymodbus catches the CancelledError raised into an in-flight request and
        re-raises it as ModbusIOException("Request cancelled outside library").
        If we treated that as an ordinary communication error we would retry
        while Home Assistant is trying to shut us down, so translate it back.
        """
        if _CANCELLED_MARKER not in str(exc):
            return
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            raise asyncio.CancelledError from exc

    async def _do_with_connection(self, func: Callable[[], Awaitable[Any]]) -> Any:
        """Execute a Modbus operation with automatic retry and connection management.

        The Blauberg S21 only supports a single TCP connection at a time, so a
        lock serialises access and the socket is always closed again before we
        return.

        Two rules are critical here and are the reason this looks the way it
        does:

        1. ``connect()`` is never wrapped in ``asyncio.wait_for``. Cancelling it
           mid-handshake orphans an ESTABLISHED socket that ``close()`` can no
           longer reclaim, which locks the integration out of its own device
           until the Home Assistant process exits. pymodbus already bounds
           ``connect()`` internally (measured: <= 2.7 s to fail).
        2. Every exit path closes the client, so we never leave a socket behind
           that the device would keep reserved.
        """
        async with self.lock:
            if self._closed:
                raise ModbusCommunicationException(
                    f"Client for {self.host}:{self.port} is closed"
                )

            deadline = time.monotonic() + _OPERATION_DEADLINE
            last_exception: Optional[Exception] = None

            for attempt in range(1, _MAX_ATTEMPTS + 1):
                if attempt > 1:
                    delay = _RETRY_BACKOFF[min(attempt - 2, len(_RETRY_BACKOFF) - 1)]
                    if time.monotonic() + delay >= deadline:
                        _LOGGER.debug(
                            "Operation deadline reached for %s:%s, not retrying",
                            self.host,
                            self.port,
                        )
                        break
                    _LOGGER.debug(
                        "Retry %d/%d for %s:%s in %.2fs",
                        attempt,
                        _MAX_ATTEMPTS,
                        self.host,
                        self.port,
                        delay,
                    )
                    await asyncio.sleep(delay)

                if self._closed:
                    break

                # Always start from a clean slate; a client instance is cheap.
                self._close_client()
                self._client = self._create_client()

                try:
                    # NOTE: deliberately no asyncio.wait_for here - see docstring.
                    connected = await self._client.connect()
                    if not connected:
                        raise ModbusCommunicationException(
                            f"Failed to open Modbus TCP connection to "
                            f"{self.host}:{self.port}"
                        )
                    result = await func()
                except asyncio.CancelledError:
                    raise
                except UnsupportedDeviceException:
                    # Not a communication problem - retrying cannot help.
                    raise
                except Exception as exc:
                    self._reraise_if_cancelled(exc)
                    last_exception = exc
                    _LOGGER.log(
                        logging.DEBUG if attempt < _MAX_ATTEMPTS else logging.WARNING,
                        "Modbus operation failed (attempt %d/%d) for %s:%s: %s",
                        attempt,
                        _MAX_ATTEMPTS,
                        self.host,
                        self.port,
                        exc,
                    )
                    continue
                else:
                    return result
                finally:
                    # Unconditionally hand the socket back to the device.
                    self._close_client()

            # All attempts exhausted.
            if isinstance(self.device, ClimateDevice):
                self.device.available = False

            raise ModbusCommunicationException(
                f"Failed to communicate with {self.host}:{self.port} after "
                f"{_MAX_ATTEMPTS} attempts: {last_exception}"
            ) from last_exception

    def _get_registers(self, response: Any, count: int, operation: str) -> List[int]:
        registers = getattr(self._validate_modbus_response(response, operation), "registers", None)
        if not isinstance(registers, list) or len(registers) < count:
            raise ModbusCommunicationException(
                f"Modbus {operation} failed: expected {count} registers"
            )
        return registers

    def _get_bits(self, response: Any, count: int, operation: str) -> List[bool]:
        bits = getattr(self._validate_modbus_response(response, operation), "bits", None)
        if not isinstance(bits, list) or len(bits) < count:
            raise ModbusCommunicationException(
                f"Modbus {operation} failed: expected {count} coil bits"
            )
        return bits

    async def _read_input_registers(self, address: int, count: int) -> List[int]:
        response = await self._client.read_input_registers(address, count=count)
        return self._get_registers(response, count, f"read input registers at {address}")

    async def _read_holding_registers(self, address: int, count: int) -> List[int]:
        response = await self._client.read_holding_registers(address, count=count)
        return self._get_registers(response, count, f"read holding registers at {address}")

    async def _read_coils(self, address: int, count: int) -> List[bool]:
        response = await self._client.read_coils(address, count=count)
        return self._get_bits(response, count, f"read coils at {address}")

    async def _read_discrete_inputs(self, address: int, count: int) -> list[bool]:
        response = await self._client.read_discrete_inputs(address, count=count)
        return self._get_bits(response, count, f"read discrete inputs at {address}")

    async def _write_register(self, address: int, value: int) -> None:
        response = await self._client.write_register(address, value)
        self._validate_modbus_response(response, f"write register {address}")

    async def _write_coil(self, address: int, value: bool) -> None:
        response = await self._client.write_coil(address, value)
        self._validate_modbus_response(response, f"write coil {address}")

    @staticmethod
    def _validate_modbus_response(response: Any, operation: str) -> Any:
        if response is None:
            raise ModbusCommunicationException(f"Modbus {operation} failed: empty response")

        is_error = getattr(response, "isError", None)
        if callable(is_error) and response.isError():
            raise ModbusCommunicationException(
                f"Modbus {operation} failed: {response!r}"
            )

        return response
