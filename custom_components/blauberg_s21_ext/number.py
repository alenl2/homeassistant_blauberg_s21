"""Support for Blauberg S21 manual fan speed control."""
from typing import Optional

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pybls21.client import S21Client

from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up a Blauberg S21 manual fan speed number entity."""
    client: S21Client = hass.data[DOMAIN][config_entry.entry_id]

    entities = [BlS21ManualFanSpeedNumber(client)]
    async_add_entities(entities, True)


class BlS21ManualFanSpeedNumber(NumberEntity):
    """Representation of the Blauberg S21 manual (custom) fan speed percentage.

    Only takes effect while the climate entity's fan_mode is set to
    "custom" (device fan speed mode 255); otherwise the device is driven
    by one of its preset speed levels and ignores this value.
    """

    _attr_translation_key = "s21manualfanspeed"
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "%"
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:fan-speed-2"

    def __init__(self, client: S21Client) -> None:
        self._client = client

    @property
    def available(self) -> bool:
        if self._client.device:
            return self._client.device.available
        return False

    @property
    def name(self) -> Optional[str]:
        if self._client.device:
            return f"{self._client.device.name} Manual Fan Speed"

    @property
    def unique_id(self) -> Optional[str]:
        if self._client.device:
            return f"{self._client.device.unique_id}_manual_fan_speed"

    @property
    def native_value(self) -> Optional[float]:
        if self._client.device:
            return self._client.device.manual_fan_speed_percent

    async def async_set_native_value(self, value: float) -> None:
        await self._client.set_manual_fan_speed_percent(int(value))

    async def async_update(self) -> None:
        await self._client.poll()
