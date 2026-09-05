"""A fake Blauberg S21 Modbus TCP server for tests.

The real unit has one behaviour that dominates the design of the client: it
accepts exactly **one** TCP connection and answers every further connection
attempt with a TCP reset, keeping that single slot reserved for as long as the
peer holds the socket open.

This server reproduces that, which lets the socket-lifecycle regressions be
tested deterministically on any machine with no hardware involved. It binds to
the loopback interface on an ephemeral port, so no addresses need configuring.

Only the function codes the client actually uses are implemented:

    0x01 read coils            0x05 write single coil
    0x02 read discrete inputs  0x06 write single register
    0x03 read holding registers
    0x04 read input registers
"""
from __future__ import annotations

import asyncio
import contextlib
import struct
from enum import Enum
from typing import Final

# Modbus exception codes
ILLEGAL_DATA_ADDRESS: Final = 0x02
SERVER_DEVICE_FAILURE: Final = 0x04

COIL_COUNT: Final = 32
DISCRETE_COUNT: Final = 128
HOLDING_COUNT: Final = 128
INPUT_COUNT: Final = 64


class Fault(Enum):
    """Fault injection modes."""

    NONE = "none"
    REFUSE_CONNECTIONS = "refuse_connections"
    """Reset every incoming connection, as the unit does when its slot is busy."""
    HANG = "hang"
    """Accept the connection but never answer a request."""
    EXCEPTION_RESPONSE = "exception_response"
    """Answer every request with a Modbus exception."""
    DROP_MID_REQUEST = "drop_mid_request"
    """Close the connection instead of answering."""
    WRONG_DEVICE_TYPE = "wrong_device_type"
    """Report a device type the client must reject as unsupported."""


def _default_registers() -> tuple[list[bool], list[bool], list[int], list[int]]:
    """Build a register map that mirrors a real 3-speed S21."""
    coils = [False] * COIL_COUNT
    discrete = [False] * DISCRETE_COUNT
    holding = [0] * HOLDING_COUNT
    inputs = [0] * INPUT_COUNT

    # Coils: 0 power, 1 main timer, 2 weekly schedule, 3 boost status
    coils[0] = True

    # Holding registers
    holding[1] = 3      # HR_MaxSPEED_MODE
    holding[2] = 2      # HR_SPEED_MODE (current manual level)
    holding[17] = 50    # HR_ManualSPEED (percent)
    holding[43] = 1     # HR_OPERATION_MODE (1 = heat)
    holding[44] = 20    # HR_SetTEMP
    holding[57] = 1     # HR_BYPASS_ROTOR_TYPE (1 = bypass fitted)
    holding[74] = 2     # HR_BYPASS_ROTOR_MODE (2 = auto)

    # Input registers (temperatures are tenths of a degree)
    inputs[1] = 210     # IR_CurTEMP_SuAirIn   21.0 C
    inputs[2] = 232     # IR_CurTEMP_SuAirOut  23.2 C
    inputs[3] = 220     # IR_CurTEMP_ExAirIn   22.0 C
    inputs[4] = 150     # IR_CurTEMP_ExAirOut  15.0 C
    inputs[10] = 45     # IR_CurRH_Int
    inputs[21] = 0      # IR_CurSuPRESS
    inputs[22] = 0      # IR_CurExPRESS
    inputs[23] = 2460   # IR_SuRPM
    inputs[24] = 2520   # IR_ExRPM
    inputs[25] = 0      # IR_CurTIMER_TIME (minutes << 8 | seconds)
    inputs[26] = 0      # IR_CurTIMER_TIME_HRS
    inputs[28] = 90     # IR_CurFILTER_TIMER (days)
    inputs[31] = 0      # IR_StateFILTER
    inputs[32] = 1      # IR_CurWeekSpeed
    # Firmware 1.9, dated 2024-06-28. The client decodes register 34 as
    # (major, minor), 35 as (day, month) and 36 as the year.
    inputs[34] = (1 << 8) | 9
    inputs[35] = (28 << 8) | 6
    inputs[36] = 2024
    inputs[37] = 1      # IR_DeviceTYPE - 1 means "supported"
    inputs[38] = 0      # IR_ALARM

    return coils, discrete, holding, inputs


class FakeS21Server:
    """An asyncio Modbus TCP server that behaves like a Blauberg S21."""

    def __init__(self, *, single_connection: bool = True) -> None:
        self.single_connection = single_connection
        self.fault = Fault.NONE
        self.response_delay = 0.0

        self.coils, self.discrete, self.holding, self.inputs = _default_registers()

        # Observability for assertions
        self.connections_accepted = 0
        self.connections_refused = 0
        self.requests_served = 0
        self.writes: list[tuple[str, int, int]] = []
        self.peak_concurrent = 0

        self._server: asyncio.AbstractServer | None = None
        self._active = 0
        self._host = "127.0.0.1"
        self._port = 0

    # ------------------------------------------------------------------ setup
    async def start(self) -> tuple[str, int]:
        """Start listening on the loopback interface and return (host, port)."""
        self._server = await asyncio.start_server(
            self._handle, self._host, 0, backlog=8
        )
        self._port = self._server.sockets[0].getsockname()[1]
        return self._host, self._port

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        # pragma: no cover - wait_closed can raise on some platforms
        with contextlib.suppress(Exception):
            await self._server.wait_closed()
        self._server = None

    @property
    def address(self) -> tuple[str, int]:
        return self._host, self._port

    @property
    def active_connections(self) -> int:
        return self._active

    async def wait_idle(self, timeout: float = 2.0) -> None:
        """Wait until no connection is open.

        A client closing its socket only becomes visible here once the server's
        handler task has been scheduled and unwound, so tests must await this
        rather than reading :attr:`active_connections` straight after an
        operation returns.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while self._active > 0:
            if loop.time() > deadline:
                raise AssertionError(
                    f"{self._active} connection(s) still open after {timeout}s"
                )
            await asyncio.sleep(0.01)

    def reset_counters(self) -> None:
        self.connections_accepted = 0
        self.connections_refused = 0
        self.requests_served = 0
        self.peak_concurrent = 0
        self.writes.clear()

    # ------------------------------------------------------------- connection
    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        if self.fault is Fault.REFUSE_CONNECTIONS or (
            self.single_connection and self._active >= 1
        ):
            self.connections_refused += 1
            # abort() sends a TCP reset rather than a graceful FIN, which is
            # what the real unit does when its only slot is taken.
            writer.transport.abort()
            return

        self._active += 1
        self.connections_accepted += 1
        self.peak_concurrent = max(self.peak_concurrent, self._active)
        try:
            await self._serve(reader, writer)
        except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
            pass
        except asyncio.CancelledError:
            raise
        finally:
            self._active -= 1
            with contextlib.suppress(Exception):
                writer.close()

    async def _serve(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        while True:
            header = await reader.readexactly(7)
            txn, _proto, length, unit = struct.unpack(">HHHB", header)
            body = await reader.readexactly(length - 1)

            if self.fault is Fault.HANG:
                await asyncio.Future()  # never resolves
            if self.fault is Fault.DROP_MID_REQUEST:
                writer.transport.abort()
                return
            if self.response_delay:
                await asyncio.sleep(self.response_delay)

            pdu = self._dispatch(body)
            self.requests_served += 1

            frame = struct.pack(">HHHB", txn, 0, len(pdu) + 1, unit) + pdu
            writer.write(frame)
            await writer.drain()

    # ---------------------------------------------------------------- decoding
    def _dispatch(self, body: bytes) -> bytes:
        function = body[0]

        if self.fault is Fault.EXCEPTION_RESPONSE:
            return bytes([function | 0x80, SERVER_DEVICE_FAILURE])

        try:
            if function in (0x01, 0x02, 0x03, 0x04):
                address, count = struct.unpack(">HH", body[1:5])
                return self._read(function, address, count)
            if function == 0x05:
                address, raw = struct.unpack(">HH", body[1:5])
                return self._write_coil(address, raw)
            if function == 0x06:
                address, value = struct.unpack(">HH", body[1:5])
                return self._write_register(address, value)
        except IndexError:
            return bytes([function | 0x80, ILLEGAL_DATA_ADDRESS])

        return bytes([function | 0x80, 0x01])  # illegal function

    def _read(self, function: int, address: int, count: int) -> bytes:
        if function in (0x01, 0x02):
            source = self.coils if function == 0x01 else self.discrete
            if address + count > len(source):
                return bytes([function | 0x80, ILLEGAL_DATA_ADDRESS])
            bits = source[address : address + count]
            packed = bytearray((count + 7) // 8)
            for index, bit in enumerate(bits):
                if bit:
                    packed[index // 8] |= 1 << (index % 8)
            return bytes([function, len(packed)]) + bytes(packed)

        source = self.holding if function == 0x03 else self.inputs
        if address + count > len(source):
            return bytes([function | 0x80, ILLEGAL_DATA_ADDRESS])
        values = list(source[address : address + count])
        if (
            function == 0x04
            and self.fault is Fault.WRONG_DEVICE_TYPE
            and address <= 37 < address + count
        ):
            values[37 - address] = 99
        payload = b"".join(struct.pack(">H", value & 0xFFFF) for value in values)
        return bytes([function, len(payload)]) + payload

    def _write_coil(self, address: int, raw: int) -> bytes:
        if address >= len(self.coils):
            return bytes([0x85, ILLEGAL_DATA_ADDRESS])
        value = raw == 0xFF00
        self.coils[address] = value
        self.writes.append(("coil", address, int(value)))
        # A few control coils drive a separate status coil on the real unit.
        if address == 13:      # CL_BoostSWITCH_CTRL -> CL_Boost_MODE
            self.coils[3] = value
        return struct.pack(">BHH", 0x05, address, raw)

    def _write_register(self, address: int, value: int) -> bytes:
        if address >= len(self.holding):
            return bytes([0x86, ILLEGAL_DATA_ADDRESS])
        self.holding[address] = value
        self.writes.append(("register", address, value))
        return struct.pack(">BHH", 0x06, address, value)
