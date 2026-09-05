"""Switch entities for the Blauberg S21 integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BlaubergS21Coordinator, get_data
from .entity import BlaubergS21Entity


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Blauberg S21 switches."""
    coordinator = get_data(hass, config_entry).coordinator
    async_add_entities(
        [
            BlaubergS21BoostSwitch(coordinator),
            BlaubergS21TimerSwitch(coordinator),
            BlaubergS21ScheduleModeSwitch(coordinator),
        ]
    )


class BlaubergS21BoostSwitch(BlaubergS21Entity, SwitchEntity):
    """Boost mode."""

    _attr_icon = "mdi:fan-plus"
    _attr_translation_key = "blauberg_s21_boost_switch"

    def __init__(self, coordinator: BlaubergS21Coordinator) -> None:
        super().__init__(coordinator, key="boost_switch")

    @property
    def is_on(self) -> bool | None:
        return self.device.is_boosting if self.device else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_call(self.client.set_boost_on)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_call(self.client.set_boost_off)


class BlaubergS21TimerSwitch(BlaubergS21Entity, SwitchEntity):
    """Main timer mode."""

    _attr_icon = "mdi:timer"
    _attr_translation_key = "blauberg_s21_timer_switch"

    def __init__(self, coordinator: BlaubergS21Coordinator) -> None:
        super().__init__(coordinator, key="timer_switch")

    @property
    def is_on(self) -> bool | None:
        return self.device.is_timer if self.device else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_call(self.client.set_timer_on)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_call(self.client.set_timer_off)


class BlaubergS21ScheduleModeSwitch(BlaubergS21Entity, SwitchEntity):
    """Weekly schedule mode."""

    _attr_icon = "mdi:calendar-clock"
    _attr_translation_key = "blauberg_s21_schedule_mode_switch"

    def __init__(self, coordinator: BlaubergS21Coordinator) -> None:
        super().__init__(coordinator, key="schedule_mode_switch")

    @property
    def is_on(self) -> bool | None:
        return self.device.is_schedule_mode if self.device else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_call(self.client.set_scheduler_mode_on)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_call(self.client.set_scheduler_mode_off)
