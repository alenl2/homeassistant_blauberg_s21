"""The Blauberg S21 integration."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN
from .pybls21.client import S21Client
from .pybls21.exceptions import UnsupportedDeviceException
from .pybls21.models import ClimateDevice

_LOGGER = logging.getLogger(__name__)

# A full poll of the unit takes ~115 ms, so polling is cheap. 30 s keeps the
# entities responsive without hammering the single-connection Modbus server.
SCAN_INTERVAL = timedelta(seconds=30)

# The unit only accepts one TCP connection and keeps that slot reserved for as
# long as a peer holds the socket open. Short outages therefore happen in normal
# life (the vendor app connecting, a Wi-Fi hiccup, the unit being busy). Rather
# than flapping every entity to "unavailable" for a single missed poll, tolerate
# a few consecutive failures first. 3 * 30 s = 90 s of grace.
FAILURE_GRACE_COUNT = 3

PLATFORMS: list[Platform] = [
    Platform.CLIMATE,
    Platform.BUTTON,
    Platform.SWITCH,
    Platform.SELECT,
]


class BlaubergS21Coordinator(DataUpdateCoordinator):
    """Coordinator that tolerates a short burst of communication failures.

    ``last_update_success`` flips to ``False`` on the first failed poll, which
    would immediately mark every entity unavailable. Because this device is
    known to reserve its single TCP slot for up to a minute after an unclean
    disconnect, entities instead stay available (serving the last known state)
    until ``FAILURE_GRACE_COUNT`` consecutive polls have failed.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: S21Client,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {client.host}:{client.port}",
            update_interval=SCAN_INTERVAL,
        )
        self.config_entry = entry
        self.client = client
        self._consecutive_failures = 0

    @property
    def device_reachable(self) -> bool:
        """Return True while the device should still be considered usable."""
        if self.last_update_success:
            return True
        return 0 < self._consecutive_failures < FAILURE_GRACE_COUNT

    async def _async_update_data(self) -> ClimateDevice:
        try:
            device = await self.client.poll()
        except UnsupportedDeviceException:
            # Not recoverable by retrying - let it bubble up as a hard error.
            raise
        except Exception as err:
            self._consecutive_failures += 1
            raise UpdateFailed(
                f"Error communicating with Blauberg S21 at "
                f"{self.client.host}:{self.client.port} "
                f"(consecutive failure {self._consecutive_failures}): {err}"
            ) from err

        if self._consecutive_failures:
            _LOGGER.debug(
                "Communication recovered after %d consecutive failure(s)",
                self._consecutive_failures,
            )
        self._consecutive_failures = 0
        return device


@dataclass
class BlaubergS21Data:
    """Runtime data for a configured Blauberg S21."""

    client: S21Client
    coordinator: BlaubergS21Coordinator


def get_data(hass: HomeAssistant, entry: ConfigEntry) -> BlaubergS21Data:
    """Return the runtime data for a config entry."""
    return hass.data[DOMAIN][entry.entry_id]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Blauberg S21 from a config entry."""
    host: str = entry.data[CONF_HOST]
    port: int = entry.data[CONF_PORT]

    client = S21Client(host, port)
    coordinator = BlaubergS21Coordinator(hass, entry, client)

    # Home Assistant does NOT unload config entries on shutdown; on
    # EVENT_HOMEASSISTANT_STOP it only cancels pending setup retries. Without
    # this listener the Modbus socket is left for the OS to tear down, and the
    # unit can keep its single connection slot reserved well past the restart -
    # which is exactly what made the entities come back unavailable.
    @callback
    def _handle_hass_stop(_event: Event) -> None:
        _LOGGER.debug("Home Assistant is stopping, closing Modbus socket")
        client.close_nowait()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _handle_hass_stop)
    )
    # Also close the socket whenever the entry itself is unloaded or reloaded,
    # including when the first refresh below raises ConfigEntryNotReady.
    entry.async_on_unload(client.close_nowait)
    # Guarantee the polling timer and debouncer are torn down on unload on every
    # supported Home Assistant version (newer cores also do this themselves,
    # and async_shutdown is idempotent).
    entry.async_on_unload(coordinator.async_shutdown)

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = BlaubergS21Data(
        client=client, coordinator=coordinator
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        domain_data = hass.data.get(DOMAIN, {})
        data: BlaubergS21Data | None = domain_data.pop(entry.entry_id, None)
        if not domain_data:
            hass.data.pop(DOMAIN, None)
        if data is not None:
            # entry.async_on_unload already closed the socket; awaiting the
            # lock-protected close guarantees any in-flight poll has finished
            # before we return.
            await data.client.close()

    return unload_ok
