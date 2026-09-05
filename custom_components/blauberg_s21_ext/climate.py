"""Support for climate device."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.components.climate.const import (
    FAN_HIGH,
    FAN_LOW,
    FAN_MEDIUM,
    FAN_OFF,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BlaubergS21Coordinator, get_data
from .const import DOMAIN
from .entity import BlaubergS21Entity
from .pybls21.models import HVACAction as BlS21HVACAction
from .pybls21.models import HVACMode as BlS21HVACMode

_LOGGER = logging.getLogger(__name__)

HA_TO_S21_HVACMODE = {
    HVACMode.OFF: BlS21HVACMode.OFF,
    HVACMode.HEAT: BlS21HVACMode.HEAT,
    HVACMode.COOL: BlS21HVACMode.COOL,
    HVACMode.AUTO: BlS21HVACMode.AUTO,
    HVACMode.FAN_ONLY: BlS21HVACMode.FAN_ONLY,
}

S21_TO_HA_HVACMODE = {v: k for k, v in HA_TO_S21_HVACMODE.items()}

S21_TO_HA_HVACACTION = {
    BlS21HVACAction.COOLING: HVACAction.COOLING,
    BlS21HVACAction.FAN: HVACAction.FAN,
    BlS21HVACAction.HEATING: HVACAction.HEATING,
    BlS21HVACAction.IDLE: HVACAction.IDLE,
    BlS21HVACAction.OFF: HVACAction.OFF,
}

FAN_CUSTOM = "custom"

# Only the named speeds are mapped; anything else is surfaced as its raw level
# so that devices with a max_fan_level other than 3 still work.
S21_TO_HA_FAN_MODE = {
    0: FAN_OFF,
    1: FAN_LOW,
    2: FAN_MEDIUM,
    3: FAN_HIGH,
    255: FAN_CUSTOM,
}
HA_TO_S21_FAN_MODE = {v: k for k, v in S21_TO_HA_FAN_MODE.items()}


def _fan_level_to_ha(level: int | None, max_fan_level: int | None) -> str | None:
    """Translate a raw device fan level into a Home Assistant fan mode."""
    if level is None:
        return None
    # The friendly low/medium/high labels only make sense on a 3-speed unit.
    if max_fan_level == 3 or level in (0, 255):
        mapped = S21_TO_HA_FAN_MODE.get(level)
        if mapped is not None:
            return mapped
    return str(level)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up a Blauberg S21 climate entity."""
    coordinator = get_data(hass, config_entry).coordinator

    async_add_entities([BlS21ClimateEntity(coordinator)])

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        "reset_filter_change_timer",
        None,
        "async_reset_filter_change_timer",
    )
    platform.async_register_entity_service(
        "reset_alarm",
        None,
        "async_reset_alarm",
    )


class BlS21ClimateEntity(BlaubergS21Entity, ClimateEntity):
    """Representation of a Blauberg S21 climate feature."""

    _attr_name = None
    _attr_translation_key = "s21climate"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.TURN_OFF
        | ClimateEntityFeature.TURN_ON
    )

    def __init__(self, coordinator: BlaubergS21Coordinator) -> None:
        super().__init__(coordinator, key=None)

    @property
    def precision(self) -> float:
        if (device := self.device) is not None and device.precision:
            return device.precision
        return 1.0

    @property
    def current_temperature(self) -> float | None:
        return self.device.current_temperature if self.device else None

    @property
    def target_temperature(self) -> float | None:
        return self.device.target_temperature if self.device else None

    @property
    def target_temperature_step(self) -> float:
        if (device := self.device) is not None and device.target_temperature_step:
            return device.target_temperature_step
        return 1.0

    @property
    def max_temp(self) -> float:
        if (device := self.device) is not None and device.max_temp is not None:
            return device.max_temp
        return 30.0

    @property
    def min_temp(self) -> float:
        if (device := self.device) is not None and device.min_temp is not None:
            return device.min_temp
        return 15.0

    @property
    def current_humidity(self) -> float | None:
        return self.device.current_humidity if self.device else None

    @property
    def hvac_mode(self) -> HVACMode | None:
        if (device := self.device) is None:
            return None
        return S21_TO_HA_HVACMODE.get(device.hvac_mode)

    @property
    def hvac_action(self) -> HVACAction | None:
        if (device := self.device) is None:
            return None
        return S21_TO_HA_HVACACTION.get(device.hvac_action)

    @property
    def hvac_modes(self) -> list[HVACMode]:
        if (device := self.device) is None:
            return []
        return [
            S21_TO_HA_HVACMODE[mode]
            for mode in device.hvac_modes
            if mode in S21_TO_HA_HVACMODE
        ]

    @property
    def fan_mode(self) -> str | None:
        if (device := self.device) is None:
            return None
        return _fan_level_to_ha(device.fan_mode, device.max_fan_level)

    @property
    def fan_modes(self) -> list[str]:
        if (device := self.device) is None or not device.fan_modes:
            return []
        return [
            _fan_level_to_ha(level, device.max_fan_level) or str(level)
            for level in device.fan_modes
        ]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        if (device := self.device) is None:
            return {}
        return {
            "current_intake_temperature_in": device.current_intake_temperature,
            "current_intake_temperature_out": device.current_intake_temperature_out,
            "current_outlet_temperature_in": device.current_outlet_temperature_in,
            "current_outlet_temperature_out": device.current_outlet_temperature_out,
            "alarm_state": device.alarm_state,
            "alarm_codes": device.alarm_codes,
            "bypass_type": device.bypass_type,
            "bypass_mode": device.bypass_mode,
            "filter_state": device.filter_state,
            "filter_countdown": device.filter_countdown,
            "pressure_air_incoming": device.pressure_air_incoming,
            "pressure_air_outgoing": device.pressure_air_outgoing,
            "supply_fan_speed": device.supply_fan_speed,
            "extract_fan_speed": device.extract_fan_speed,
            "manual_fan_speed_percent": device.manual_fan_speed_percent,
            "max_fan_level": device.max_fan_level,
            "is_boosting": device.is_boosting,
            "is_timer": device.is_timer,
            "timer_countdown": device.timer_countdown,
            "is_schedule_mode": device.is_schedule_mode,
            "fan_level_schedule_mode": _fan_level_to_ha(
                device.fan_level_schedule_mode, device.max_fan_level
            ),
            "fan_level_manual_mode": _fan_level_to_ha(
                device.fan_level_manual_mode, device.max_fan_level
            ),
        }

    @property
    def icon(self) -> str | None:
        if (device := self.device) is None or not self.available:
            return "mdi:lan-disconnect"
        if device.is_boosting:
            return "mdi:fan-plus"
        if device.hvac_action == BlS21HVACAction.OFF:
            return "mdi:fan-off"
        if device.hvac_action == BlS21HVACAction.IDLE:
            return "mdi:fan-remove"
        if device.max_fan_level == 3:
            if device.fan_mode == 1:
                return "mdi:fan-speed-1"
            if device.fan_mode == 2:
                return "mdi:fan-speed-2"
            if device.fan_mode == 3:
                return "mdi:fan-speed-3"
        if device.hvac_action == BlS21HVACAction.COOLING:
            return "mdi:fan-chevron-down"
        if device.hvac_action == BlS21HVACAction.HEATING:
            return "mdi:fan-chevron-up"
        return "mdi:fan"

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode not in HA_TO_S21_HVACMODE:
            raise ServiceValidationError(f"Unsupported hvac mode: {hvac_mode}")
        await self._async_call(
            self.client.set_hvac_mode, HA_TO_S21_HVACMODE[hvac_mode]
        )

    async def async_turn_on(self) -> None:
        """Power the unit on without changing its operating mode.

        ClimateEntity's fallback would pick HVACMode.HEAT and force the heater
        on, so drive the power coil directly instead.
        """
        await self._async_call(self.client.turn_on)

    async def async_turn_off(self) -> None:
        """Power the unit off."""
        await self._async_call(self.client.turn_off)

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        device = self.device
        max_fan_level = device.max_fan_level if device else None

        level = HA_TO_S21_FAN_MODE.get(fan_mode)
        if level is None:
            try:
                level = int(fan_mode)
            except (TypeError, ValueError) as err:
                raise ServiceValidationError(
                    f"Unsupported fan mode: {fan_mode}"
                ) from err
        if level == 0:
            # "off" is not a settable speed on this unit; power it off instead.
            await self.async_turn_off()
            return

        previous_fan_mode = self.fan_mode
        await self._async_call(self.client.set_fan_mode, level, max_fan_level)

        current_fan_mode = self.fan_mode
        if (
            previous_fan_mode is not None
            and current_fan_mode is not None
            and previous_fan_mode != current_fan_mode
        ):
            self.hass.bus.async_fire(
                "logbook_entry",
                {
                    "name": self.device_name,
                    "message": (
                        f"Fan mode changed: {previous_fan_mode} -> {current_fan_mode}"
                    ),
                    "entity_id": self.entity_id,
                    "domain": DOMAIN,
                },
            )

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        target = round(float(temperature))
        if not self.min_temp <= target <= self.max_temp:
            raise ServiceValidationError(
                f"Temperature {target} °C is outside the supported range "
                f"{self.min_temp:.0f}-{self.max_temp:.0f} °C"
            )
        await self._async_call(self.client.set_temperature, target)

    async def async_reset_filter_change_timer(self) -> None:
        await self._async_call(self.client.reset_filter_change_timer)

    async def async_reset_alarm(self) -> None:
        await self._async_call(self.client.reset_alarm)
