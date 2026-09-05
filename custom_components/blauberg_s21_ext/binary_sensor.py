"""Status binary sensors for the Blauberg S21 integration.

Both underlying registers are multi-valued, and no documentation of their exact
values could be found — neither the vendored client, the upstream library nor the
upstream integration decodes them, and the reference unit cannot be made to cycle
through them without clearing a real alarm.

What *is* well established is which value means "nothing to report":

* ``IR_ALARM`` is zero when there is nothing wrong. The client itself relies on
  this, only bothering to read the alarm code bits when the register is above
  zero.
* ``IR_StateFILTER`` is zero on a healthy unit; the register definition
  describes it as "clean, error, replacement".

So these are exposed as problem binary sensors, which needs only that much, with
the raw register value kept as an attribute so the distinction between the
non-zero states is not thrown away.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BlaubergS21Coordinator, get_data
from .entity import BlaubergS21Entity
from .pybls21.models import ClimateDevice


@dataclass(frozen=True, kw_only=True)
class BlaubergS21BinarySensorDescription(BinarySensorEntityDescription):
    """Describes a Blauberg S21 status binary sensor."""

    is_on_fn: Callable[[ClimateDevice], bool | None]
    attributes_fn: Callable[[ClimateDevice], dict[str, Any]] = field(
        default=lambda _device: {}
    )


def _raised(value: int | None) -> bool | None:
    """Zero means nothing to report; anything else needs attention."""
    if value is None:
        return None
    return value > 0


BINARY_SENSORS: tuple[BlaubergS21BinarySensorDescription, ...] = (
    BlaubergS21BinarySensorDescription(
        key="alarm",
        translation_key="s21_alarm",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda device: _raised(device.alarm_state),
        attributes_fn=lambda device: {
            "alarm_state": device.alarm_state,
            "alarm_codes": device.alarm_codes,
        },
    ),
    BlaubergS21BinarySensorDescription(
        key="filter",
        translation_key="s21_filter",
        device_class=BinarySensorDeviceClass.PROBLEM,
        icon="mdi:air-filter",
        is_on_fn=lambda device: _raised(device.filter_state),
        attributes_fn=lambda device: {
            "filter_state": device.filter_state,
            "days_remaining": device.filter_countdown,
        },
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Blauberg S21 status binary sensors."""
    coordinator = get_data(hass, config_entry).coordinator
    async_add_entities(
        BlaubergS21BinarySensor(coordinator, description)
        for description in BINARY_SENSORS
    )


class BlaubergS21BinarySensor(BlaubergS21Entity, BinarySensorEntity):
    """A status register reported as a problem flag."""

    entity_description: BlaubergS21BinarySensorDescription

    def __init__(
        self,
        coordinator: BlaubergS21Coordinator,
        description: BlaubergS21BinarySensorDescription,
    ) -> None:
        super().__init__(coordinator, key=description.key)
        self.entity_description = description
        self._attr_translation_key = description.translation_key

    @property
    def is_on(self) -> bool | None:
        if (device := self.device) is None:
            return None
        return self.entity_description.is_on_fn(device)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the raw register, since "on" collapses several states."""
        if (device := self.device) is None:
            return {}
        return self.entity_description.attributes_fn(device)
