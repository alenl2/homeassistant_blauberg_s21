"""Sensors for the Blauberg S21 integration.

The temperature sensors were ported from jvitkauskas/homeassistant_blauberg_s21
(Stefan Hackenberg). The original targeted a newer pybls21 whose temperature
properties are named differently from the client vendored here, so each sensor
maps explicitly onto the register this fork's client exposes:

    supply outdoor    IR_CurTEMP_SuAirIn    outdoor air entering the unit
    supply            IR_CurTEMP_SuAirOut   air the unit delivers to the rooms
    extract           IR_CurTEMP_ExAirIn    air drawn out of the rooms
    extract outlet    IR_CurTEMP_ExAirOut   air the unit exhausts outside

The remaining readings are polled anyway, so they are exposed here too rather
than living only as climate attributes, where Home Assistant cannot record
long-term statistics for them.
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
from homeassistant.const import (
    PERCENTAGE,
    REVOLUTIONS_PER_MINUTE,
    EntityCategory,
    UnitOfPressure,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BlaubergS21Coordinator, get_data
from .entity import BlaubergS21Entity
from .pybls21.models import ClimateDevice


@dataclass(frozen=True, kw_only=True)
class BlaubergS21SensorDescription(SensorEntityDescription):
    """Describes a Blauberg S21 sensor."""

    value_fn: Callable[[ClimateDevice], float | None]
    #: Only create the entity when the unit actually reports this reading.
    supported_fn: Callable[[ClimateDevice], bool] = lambda _device: True


def _temperature(
    key: str, value_fn: Callable[[ClimateDevice], float | None]
) -> BlaubergS21SensorDescription:
    return BlaubergS21SensorDescription(
        key=key,
        translation_key=f"s21_{key}",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=value_fn,
    )


TEMPERATURE_SENSORS: tuple[BlaubergS21SensorDescription, ...] = (
    _temperature(
        "supply_outdoor_temperature",
        lambda device: device.current_intake_temperature,
    ),
    _temperature(
        "supply_temperature",
        lambda device: device.current_intake_temperature_out,
    ),
    _temperature(
        "extract_temperature",
        lambda device: device.current_outlet_temperature_in,
    ),
    _temperature(
        "extract_outlet_temperature",
        lambda device: device.current_outlet_temperature_out,
    ),
)

OTHER_SENSORS: tuple[BlaubergS21SensorDescription, ...] = (
    BlaubergS21SensorDescription(
        key="humidity",
        translation_key="s21_humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda device: device.current_humidity,
        # The client reports None when the register reads zero, which means no
        # humidity sensor is fitted. Don't create an entity that can only ever
        # be unknown.
        supported_fn=lambda device: device.current_humidity is not None,
    ),
    BlaubergS21SensorDescription(
        key="supply_fan_speed",
        translation_key="s21_supply_fan_speed",
        native_unit_of_measurement=REVOLUTIONS_PER_MINUTE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:fan",
        value_fn=lambda device: device.supply_fan_speed,
    ),
    BlaubergS21SensorDescription(
        key="extract_fan_speed",
        translation_key="s21_extract_fan_speed",
        native_unit_of_measurement=REVOLUTIONS_PER_MINUTE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:fan",
        value_fn=lambda device: device.extract_fan_speed,
    ),
    # The register names only say "pressure"; pascals is the conventional unit
    # for ventilation static pressure. Units without the optional pressure
    # sensors fitted report a constant zero.
    BlaubergS21SensorDescription(
        key="supply_pressure",
        translation_key="s21_supply_pressure",
        device_class=SensorDeviceClass.PRESSURE,
        native_unit_of_measurement=UnitOfPressure.PA,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: device.pressure_air_incoming,
    ),
    BlaubergS21SensorDescription(
        key="extract_pressure",
        translation_key="s21_extract_pressure",
        device_class=SensorDeviceClass.PRESSURE,
        native_unit_of_measurement=UnitOfPressure.PA,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: device.pressure_air_outgoing,
    ),
    BlaubergS21SensorDescription(
        key="filter_countdown",
        translation_key="s21_filter_countdown",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.DAYS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:air-filter",
        value_fn=lambda device: device.filter_countdown,
    ),
)

SENSORS: tuple[BlaubergS21SensorDescription, ...] = (
    TEMPERATURE_SENSORS + OTHER_SENSORS
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Blauberg S21 sensors."""
    coordinator = get_data(hass, config_entry).coordinator
    device = coordinator.data

    async_add_entities(
        BlaubergS21Sensor(coordinator, description)
        for description in SENSORS
        if device is not None and description.supported_fn(device)
    )


class BlaubergS21Sensor(BlaubergS21Entity, SensorEntity):
    """A reading polled from the unit."""

    entity_description: BlaubergS21SensorDescription

    def __init__(
        self,
        coordinator: BlaubergS21Coordinator,
        description: BlaubergS21SensorDescription,
    ) -> None:
        # The key doubles as the unique id suffix. For the four temperatures it
        # matches the ids the upstream sensor.py produced, so existing entities
        # are not orphaned.
        super().__init__(coordinator, key=description.key)
        self.entity_description = description
        self._attr_translation_key = description.translation_key

    @property
    def native_value(self) -> float | None:
        if (device := self.device) is None:
            return None
        return self.entity_description.value_fn(device)
