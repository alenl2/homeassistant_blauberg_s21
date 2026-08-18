"""Support for temperature sensors."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pybls21.client import S21Client

from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Blauberg S21 temperature sensors."""
    client: S21Client = hass.data[DOMAIN][config_entry.entry_id]

    entities = [
        BlS21TemperatureSensor(
            client,
            config_entry,
            "current_supply_outdoor_temperature",
            "supply_outdoor_temperature",
        ),
        BlS21TemperatureSensor(
            client,
            config_entry,
            "current_supply_temperature",
            "supply_temperature",
        ),
        BlS21TemperatureSensor(
            client,
            config_entry,
            "current_extract_temperature",
            "extract_temperature",
        ),
        BlS21TemperatureSensor(
            client,
            config_entry,
            "current_extract_outlet_temperature",
            "extract_outlet_temperature",
        ),
    ]
    async_add_entities(entities, True)


class BlS21TemperatureSensor(SensorEntity):
    """Representation of a Blauberg S21 temperature sensor."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        client: S21Client,
        config_entry: ConfigEntry,
        device_property: str,
        sensor_key: str,
    ) -> None:
        self._client = client
        self._config_entry = config_entry
        self._device_property = device_property
        self._sensor_key = sensor_key
        self._attr_translation_key = f"s21_{sensor_key}"

    @property
    def available(self) -> bool:
        if self._client.device:
            return self._client.device.available
        return False

    @property
    def unique_id(self) -> str | None:
        if self._config_entry.unique_id:
            return f"{self._config_entry.unique_id}_{self._sensor_key}"
        if self._client.device:
            return f"{self._client.device.unique_id}_{self._sensor_key}"

    @property
    def device_info(self) -> DeviceInfo | None:
        if self._client.device:
            return DeviceInfo(
                identifiers={(DOMAIN, self._client.device.unique_id)},
                name=self._client.device.name,
                manufacturer=self._client.device.manufacturer,
                model=self._client.device.model,
                sw_version=self._client.device.sw_version,
            )

    @property
    def native_value(self) -> float | None:
        if self._client.device:
            return getattr(self._client.device, self._device_property, None)
        return None
