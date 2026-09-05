"""Button entities for the Blauberg S21 integration."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
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
    """Set up the Blauberg S21 buttons."""
    coordinator = get_data(hass, config_entry).coordinator
    async_add_entities(
        [
            BlaubergS21ResetFilterButton(coordinator),
            BlaubergS21ResetAlarmButton(coordinator),
        ]
    )


class BlaubergS21ResetFilterButton(BlaubergS21Entity, ButtonEntity):
    """Reset the filter replacement countdown."""

    _attr_icon = "mdi:filter-remove"
    _attr_translation_key = "blauberg_s21_reset_filter"

    def __init__(self, coordinator: BlaubergS21Coordinator) -> None:
        super().__init__(coordinator, key="reset_filter_button")

    async def async_press(self) -> None:
        await self._async_call(self.client.reset_filter_change_timer)


class BlaubergS21ResetAlarmButton(BlaubergS21Entity, ButtonEntity):
    """Reset the current alarm state."""

    _attr_icon = "mdi:alarm-off"
    _attr_translation_key = "blauberg_s21_reset_alarm"

    def __init__(self, coordinator: BlaubergS21Coordinator) -> None:
        super().__init__(coordinator, key="reset_alarm_button")

    async def async_press(self) -> None:
        await self._async_call(self.client.reset_alarm)
