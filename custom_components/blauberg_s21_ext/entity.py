"""Shared entity plumbing for the Blauberg S21 integration."""
from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import BlaubergS21Coordinator
from .const import DOMAIN
from .pybls21.client import S21Client
from .pybls21.exceptions import (
    ModbusCommunicationException,
    UnsupportedDeviceException,
)
from .pybls21.models import ClimateDevice


class BlaubergS21Entity(CoordinatorEntity[BlaubergS21Coordinator]):
    """Base class for every Blauberg S21 entity."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: BlaubergS21Coordinator,
        key: str | None,
        unique_id: str | None = None,
    ) -> None:
        """Initialise the entity.

        ``key`` is appended to the config entry unique id to form the entity
        unique id. ``unique_id`` overrides that entirely and exists purely to
        preserve the unique ids of entities created by earlier releases.
        """
        super().__init__(coordinator)
        entry = coordinator.config_entry
        base_id = entry.unique_id or entry.entry_id
        if unique_id is not None:
            self._attr_unique_id = unique_id
        elif key is None:
            self._attr_unique_id = base_id
        else:
            self._attr_unique_id = f"{base_id}_{key}"

        device = coordinator.data
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, base_id)},
            name=(device.name if device else None) or entry.title,
            manufacturer=device.manufacturer if device else None,
            model=device.model if device else None,
            sw_version=device.sw_version if device else None,
            configuration_url=None,
        )

    @property
    def client(self) -> S21Client:
        """Return the Modbus client."""
        return self.coordinator.client

    @property
    def device(self) -> ClimateDevice | None:
        """Return the last successfully polled device state."""
        return self.coordinator.data

    @property
    def device_name(self) -> str:
        """Return a human readable name for log/logbook messages."""
        device = self.device
        if device is not None and device.name:
            return device.name
        return self.coordinator.config_entry.title

    @property
    def available(self) -> bool:
        """Return True if the entity is available.

        A single failed poll does not mark the entity unavailable: the unit only
        accepts one connection, so brief outages (the vendor app connecting, a
        network hiccup) are normal and are ridden out by the coordinator's
        failure grace period.
        """
        return self.coordinator.device_reachable and self.coordinator.data is not None

    async def _async_call(
        self,
        func: Callable[..., Coroutine[Any, Any, None]],
        *args: Any,
    ) -> None:
        """Run a client command, then refresh so the UI reflects reality.

        ``async_refresh`` is used rather than ``async_request_refresh`` because
        the latter is debounced (10 s cooldown), which would leave the entity
        showing a stale value after a command and would break the before/after
        comparison used for logbook entries. A poll only costs ~115 ms.

        The refresh is deliberately skipped when the command failed: if the unit
        is unreachable the refresh would only burn another full retry budget,
        and the coordinator's next scheduled poll will pick the state up anyway.
        """
        try:
            await func(*args)
        except UnsupportedDeviceException as err:
            raise HomeAssistantError(f"Unsupported device: {err}") from err
        except ModbusCommunicationException as err:
            raise HomeAssistantError(
                f"Could not reach the Blauberg S21 at "
                f"{self.client.host}:{self.client.port}: {err}"
            ) from err
        except ValueError as err:
            # Raised by the client's own input validation.
            raise HomeAssistantError(str(err)) from err
        await self.coordinator.async_refresh()
