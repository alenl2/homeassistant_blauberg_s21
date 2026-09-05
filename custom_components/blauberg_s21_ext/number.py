"""Manual (custom) fan speed control for the Blauberg S21 integration.

Ported from jvitkauskas/homeassistant_blauberg_s21 (Zhevniak Dmytro).

The climate entity's fan modes only write the preset speed register
(HR_SPEED_MODE), so choosing "custom" put the unit into its manual speed mode
without any way to say what percentage that should be. This exposes
HR_ManualSPEED so the percentage can be set directly.
"""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BlaubergS21Coordinator, get_data
from .climate import FAN_CUSTOM
from .entity import BlaubergS21Entity


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Blauberg S21 manual fan speed control."""
    coordinator = get_data(hass, config_entry).coordinator
    async_add_entities([BlaubergS21ManualFanSpeedNumber(coordinator)])


class BlaubergS21ManualFanSpeedNumber(BlaubergS21Entity, NumberEntity):
    """The percentage the unit runs at while its fan mode is "custom".

    The value is kept settable regardless of the current fan mode, so it can be
    dialled in before switching the climate entity over to "custom"; the unit
    stores it either way and simply ignores it while running a preset speed.
    """

    _attr_translation_key = "s21manualfanspeed"
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.SLIDER
    # Not mdi:fan-speed-N: those denote the unit's discrete preset levels, which
    # is exactly what this control is not.
    _attr_icon = "mdi:speedometer"

    def __init__(self, coordinator: BlaubergS21Coordinator) -> None:
        super().__init__(coordinator, key="manual_fan_speed")

    @property
    def native_value(self) -> float | None:
        if (device := self.device) is None:
            return None
        return device.manual_fan_speed_percent

    @property
    def extra_state_attributes(self) -> dict[str, bool]:
        """Flag whether the unit is currently acting on this value."""
        device = self.device
        return {
            "active": bool(device is not None and device.fan_mode == 255),
            "fan_mode_required": FAN_CUSTOM,
        }

    async def async_set_native_value(self, value: float) -> None:
        await self._async_call(
            self.client.set_manual_fan_speed_percent, round(value)
        )
