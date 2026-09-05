"""Temperature sensors for the Blauberg S21 integration.

Ported from jvitkauskas/homeassistant_blauberg_s21 (Stefan Hackenberg). The
original targeted a newer pybls21 whose temperature properties are named
differently from the client vendored here, so each sensor maps explicitly onto
the register this fork's client exposes:

    supply outdoor    IR_CurTEMP_SuAirIn    outdoor air entering the unit
    supply            IR_CurTEMP_SuAirOut   air the unit delivers to the rooms
    extract           IR_CurTEMP_ExAirIn    air drawn out of the rooms
    extract outlet    IR_CurTEMP_ExAirOut   air the unit exhausts outside
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BlaubergS21Coordinator, get_data
from .entity import BlaubergS21Entity
from .pybls21.models import ClimateDevice


@dataclass(frozen=True, kw_only=True)
class BlaubergS21SensorDescription(SensorEntityDescription):
    """Describes a Blauberg S21 sensor."""

    value_fn: Callable[[ClimateDevice], float | None]


TEMPERATURE_SENSORS: tuple[BlaubergS21SensorDescription, ...] = (
    BlaubergS21SensorDescription(
        key="supply_outdoor_temperature",
        translation_key="s21_supply_outdoor_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda device: device.current_intake_temperature,
    ),
    BlaubergS21SensorDescription(
        key="supply_temperature",
        translation_key="s21_supply_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda device: device.current_intake_temperature_out,
    ),
    BlaubergS21SensorDescription(
        key="extract_temperature",
        translation_key="s21_extract_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda device: device.current_outlet_temperature_in,
    ),
    BlaubergS21SensorDescription(
        key="extract_outlet_temperature",
        translation_key="s21_extract_outlet_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda device: device.current_outlet_temperature_out,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Blauberg S21 sensors."""
    coordinator = get_data(hass, config_entry).coordinator
    async_add_entities(
        BlaubergS21TemperatureSensor(coordinator, description)
        for description in TEMPERATURE_SENSORS
    )


class BlaubergS21TemperatureSensor(BlaubergS21Entity, SensorEntity):
    """A temperature reading from the heat exchanger."""

    entity_description: BlaubergS21SensorDescription

    def __init__(
        self,
        coordinator: BlaubergS21Coordinator,
        description: BlaubergS21SensorDescription,
    ) -> None:
        # The key doubles as the unique id suffix, matching the ids the original
        # sensor.py produced so existing entities are not orphaned.
        super().__init__(coordinator, key=description.key)
        self.entity_description = description
        self._attr_translation_key = description.translation_key

    @property
    def native_value(self) -> float | None:
        if (device := self.device) is None:
            return None
        return self.entity_description.value_fn(device)
