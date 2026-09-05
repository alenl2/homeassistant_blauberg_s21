"""A minimal stand-in for the Home Assistant APIs this integration uses.

Why this exists
---------------
The usual way to test a custom component is
``pytest-homeassistant-custom-component``, which pulls in Home Assistant itself.
That is a large dependency and needs a C toolchain to build on some platforms,
which makes it awkward to run the suite on a maintainer's machine.

This module therefore provides just enough of the API surface for the
integration's own logic to be exercised: config entries, the event bus,
``DataUpdateCoordinator``, ``CoordinatorEntity`` and the four platform base
classes.

What it is faithful about
-------------------------
* ``DataUpdateCoordinator`` mirrors ``homeassistant/helpers/update_coordinator.py``
  for ``data`` / ``last_update_success`` bookkeeping, ``UpdateFailed`` handling,
  listener notification and ``async_config_entry_first_refresh`` raising
  ``ConfigEntryNotReady``.
* ``Entity._attr_name`` is an annotation *without* a default, exactly as in core.
  Home Assistant's naming logic keys off ``hasattr(self, "_attr_name")``, so a
  default here would silently mask the translation lookup.
* ``ClimateEntity.async_turn_on`` reproduces core's "fake turn on", which picks
  HEAT_COOL/HEAT/COOL. A test relies on that to prove the integration overrides it.

What it does NOT do
-------------------
* No entity/device registry, no state machine, no service call plumbing.
* The coordinator's interval scheduler is not simulated; tests drive refreshes
  explicitly.

If Home Assistant's helpers change materially, prefer switching these tests to
``pytest-homeassistant-custom-component`` over growing this shim.
"""
from __future__ import annotations

import asyncio
import importlib.util
import logging
import sys
import types
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine


def _module(name: str) -> types.ModuleType:
    module = types.ModuleType(name)
    sys.modules[name] = module
    parent, _, child = name.rpartition(".")
    if parent:
        setattr(sys.modules[parent], child, module)
    return module


#: True once the stub is the active ``homeassistant`` implementation.
installed = False

#: Set when a real Home Assistant was found, in which case the stub steps aside.
real_home_assistant = False


def install() -> bool:
    """Install the stub modules into ``sys.modules``.

    Does nothing and returns ``False`` if a real Home Assistant is importable,
    so the stub can never shadow a genuine installation. Safe to call repeatedly.
    """
    global installed, real_home_assistant

    if installed:
        return True
    if real_home_assistant:
        return False

    if importlib.util.find_spec("homeassistant") is not None:
        # A real Home Assistant is available; prefer it and let the caller decide
        # what to do (the integration tests skip, pointing at
        # pytest-homeassistant-custom-component).
        real_home_assistant = True
        return False

    _build()
    installed = True
    return True


class _Undefined:
    """Stand-in for homeassistant.helpers.typing.UNDEFINED."""

    def __repr__(self) -> str:
        return "UNDEFINED"

    def __bool__(self) -> bool:
        return False


UNDEFINED = _Undefined()


def _build() -> None:  # noqa: PLR0915 - one flat builder is easiest to read
    ha = _module("homeassistant")
    ha._blauberg_stub = True

    # ---------------------------------------------------------------- const
    const = _module("homeassistant.const")
    const.CONF_HOST = "host"
    const.CONF_PORT = "port"
    const.EVENT_HOMEASSISTANT_STOP = "homeassistant_stop"
    const.ATTR_TEMPERATURE = "temperature"
    const.PERCENTAGE = "%"
    const.REVOLUTIONS_PER_MINUTE = "rpm"

    class UnitOfPressure:
        PA = "Pa"
        HPA = "hPa"
        BAR = "bar"

    class UnitOfTime:
        SECONDS = "s"
        MINUTES = "min"
        HOURS = "h"
        DAYS = "d"

    class EntityCategory:
        CONFIG = "config"
        DIAGNOSTIC = "diagnostic"

    const.UnitOfPressure = UnitOfPressure
    const.UnitOfTime = UnitOfTime
    const.EntityCategory = EntityCategory

    class Platform:
        CLIMATE = "climate"
        BINARY_SENSOR = "binary_sensor"
        BUTTON = "button"
        NUMBER = "number"
        SELECT = "select"
        SENSOR = "sensor"
        SWITCH = "switch"

    class UnitOfTemperature:
        CELSIUS = "\u00b0C"
        FAHRENHEIT = "\u00b0F"

    const.Platform = Platform
    const.UnitOfTemperature = UnitOfTemperature

    # ----------------------------------------------------------- exceptions
    exceptions = _module("homeassistant.exceptions")

    class HomeAssistantError(Exception):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args)
            self.translation_domain = kwargs.get("translation_domain")
            self.translation_key = kwargs.get("translation_key")

    class ServiceValidationError(HomeAssistantError):
        pass

    class ConfigEntryNotReady(HomeAssistantError):
        pass

    class ConfigEntryError(HomeAssistantError):
        pass

    class ConfigEntryAuthFailed(HomeAssistantError):
        pass

    exceptions.HomeAssistantError = HomeAssistantError
    exceptions.ServiceValidationError = ServiceValidationError
    exceptions.ConfigEntryNotReady = ConfigEntryNotReady
    exceptions.ConfigEntryError = ConfigEntryError
    exceptions.ConfigEntryAuthFailed = ConfigEntryAuthFailed

    # ----------------------------------------------------------------- core
    core = _module("homeassistant.core")

    def callback(func):
        func._hass_callback = True
        return func

    @dataclass
    class Event:
        event_type: str
        data: dict = field(default_factory=dict)

    class EventBus:
        def __init__(self) -> None:
            self.listeners: dict[str, list[Callable]] = {}

        def async_listen_once(self, event_type: str, listener: Callable) -> Callable:
            self.listeners.setdefault(event_type, []).append(listener)

            def _remove() -> None:
                if listener in self.listeners.get(event_type, []):
                    self.listeners[event_type].remove(listener)

            return _remove

        def async_fire(self, event_type: str, data: dict | None = None) -> None:
            for listener in list(self.listeners.get(event_type, [])):
                listener(Event(event_type, data or {}))
            self.listeners.pop(event_type, None)

    class ServiceRegistry:
        def __init__(self) -> None:
            self.registered: dict[tuple[str, str], Any] = {}

        def has_service(self, domain: str, service: str) -> bool:
            return (domain, service) in self.registered

        def async_register(self, domain: str, service: str, func: Any) -> None:
            self.registered[(domain, service)] = func

    class HomeAssistant:
        def __init__(self) -> None:
            self.data: dict = {}
            self.bus = EventBus()
            self.services = ServiceRegistry()
            self.config_entries = ConfigEntries()
            self.is_stopping = False

        def async_create_background_task(self, coro, name=None, eager_start=True):
            return asyncio.ensure_future(coro)

        def async_create_task(self, coro, name=None, eager_start=True):
            return asyncio.ensure_future(coro)

        def async_add_executor_job(self, func, *args):
            return asyncio.get_event_loop().run_in_executor(None, func, *args)

    core.HomeAssistant = HomeAssistant
    core.Event = Event
    core.callback = callback

    # ------------------------------------------------------- config entries
    config_entries = _module("homeassistant.config_entries")

    class ConfigEntryState:
        LOADED = "loaded"
        SETUP_IN_PROGRESS = "setup_in_progress"
        NOT_LOADED = "not_loaded"

    class ConfigEntry:
        def __init__(
            self,
            *,
            domain: str,
            data: dict,
            title: str = "Test entry",
            unique_id: str | None = None,
            entry_id: str = "test_entry",
            options: dict | None = None,
        ) -> None:
            self.domain = domain
            self.data = data
            self.title = title
            self.unique_id = unique_id
            self.entry_id = entry_id
            self.options = options or {}
            self.state = ConfigEntryState.SETUP_IN_PROGRESS
            self.pref_disable_polling = False
            self.runtime_data: Any = None
            self.on_unload: list[Callable] = []

        def async_on_unload(self, func: Callable) -> None:
            self.on_unload.append(func)

        async def run_on_unload(self) -> None:
            """Emulate ConfigEntry._async_process_on_unload."""
            for func in reversed(self.on_unload):
                result = func()
                if asyncio.iscoroutine(result):
                    await result
            self.on_unload.clear()

        def async_create_background_task(self, hass, coro, name=None, eager_start=True):
            return asyncio.ensure_future(coro)

    class ConfigEntries:
        def __init__(self) -> None:
            self.forwarded: list[tuple[Any, list[str]]] = []
            self.unloaded: list[tuple[Any, list[str]]] = []
            self.unload_result = True

        async def async_forward_entry_setups(self, entry, platforms) -> None:
            self.forwarded.append((entry, list(platforms)))

        async def async_unload_platforms(self, entry, platforms) -> bool:
            self.unloaded.append((entry, list(platforms)))
            return self.unload_result

        async def async_reload(self, entry_id: str) -> bool:
            return True

    class ConfigFlow:
        def __init_subclass__(cls, /, domain: str | None = None, **kwargs: Any) -> None:
            super().__init_subclass__(**kwargs)
            cls.domain = domain

    config_entries.ConfigEntry = ConfigEntry
    config_entries.ConfigEntries = ConfigEntries
    config_entries.ConfigEntryState = ConfigEntryState
    config_entries.ConfigFlow = ConfigFlow
    config_entries.SOURCE_USER = "user"

    data_entry_flow = _module("homeassistant.data_entry_flow")
    data_entry_flow.FlowResult = dict

    # -------------------------------------------------------------- helpers
    _module("homeassistant.helpers")

    device_registry = _module("homeassistant.helpers.device_registry")

    class DeviceInfo(dict):
        pass

    device_registry.DeviceInfo = DeviceInfo

    entity_module = _module("homeassistant.helpers.entity")

    class Entity:
        # These are annotations without defaults on purpose - see module docstring.
        # Core declares them this way so that hasattr() is False unless a
        # subclass sets them, which is what lets entity_description supply the
        # value instead. Giving them defaults here would silently mask both the
        # translation lookup and every entity_description field.
        _attr_name: str | None
        _attr_icon: str | None
        _attr_translation_key: str | None
        _attr_entity_category: Any
        _attr_has_entity_name: bool
        # These do have defaults in core.
        _attr_unique_id: str | None = None
        _attr_device_info: Any = None
        _attr_supported_features: Any = None

        hass: Any = None
        entity_id: str | None = None

        #: Injected by tests so translated names can be resolved.
        platform_translations: dict[str, str] = {}
        platform_domain: str = ""
        integration_domain: str = ""

        def _described(self, attribute: str, default: Any = None) -> Any:
            """Mirror core's `_attr_x` then `entity_description.x` precedence."""
            if hasattr(self, f"_attr_{attribute}"):
                return getattr(self, f"_attr_{attribute}")
            if hasattr(self, "entity_description"):
                return getattr(self.entity_description, attribute, default)
            return default

        @property
        def should_poll(self) -> bool:
            return True

        @property
        def has_entity_name(self) -> bool:
            return self._described("has_entity_name", False)

        @property
        def name(self):
            """Mirror of Entity._name_internal."""
            if hasattr(self, "_attr_name"):
                return self._attr_name
            if self.has_entity_name and (key := self.translation_key):
                lookup = (
                    f"component.{self.integration_domain}.entity."
                    f"{self.platform_domain}.{key}.name"
                )
                if (found := self.platform_translations.get(lookup)) is not None:
                    return found
            return UNDEFINED

        @property
        def translation_key(self) -> str | None:
            return self._described("translation_key")

        @property
        def unique_id(self) -> str | None:
            return self._attr_unique_id

        @property
        def icon(self) -> str | None:
            return self._described("icon")

        @property
        def entity_category(self):
            return self._described("entity_category")

        @property
        def device_info(self) -> Any:
            return self._attr_device_info

        @property
        def supported_features(self) -> Any:
            return self._attr_supported_features

        @property
        def available(self) -> bool:
            return True

        @property
        def extra_state_attributes(self) -> dict:
            return {}

        def async_write_ha_state(self) -> None:
            self.state_writes = getattr(self, "state_writes", 0) + 1

        def async_on_remove(self, func: Callable) -> None:
            self.remove_callbacks = getattr(self, "remove_callbacks", [])
            self.remove_callbacks.append(func)

        async def async_added_to_hass(self) -> None:
            return None

    entity_module.Entity = Entity
    entity_module.UNDEFINED = UNDEFINED

    entity_platform = _module("homeassistant.helpers.entity_platform")

    class RegisteredPlatform:
        def __init__(self) -> None:
            self.services: list[tuple[str, Any, Any]] = []

        def async_register_entity_service(self, name, schema, func, *a, **kw) -> None:
            self.services.append((name, schema, func))

    _platform = RegisteredPlatform()

    entity_platform.AddEntitiesCallback = object
    entity_platform.current_platform = _platform
    entity_platform.async_get_current_platform = lambda: _platform

    # ---------------------------------------------------------- coordinator
    coordinator_module = _module("homeassistant.helpers.update_coordinator")

    class UpdateFailed(HomeAssistantError):
        def __init__(self, *args: Any, retry_after: float | None = None, **kw) -> None:
            super().__init__(*args)
            self.retry_after = retry_after

    class DataUpdateCoordinator:
        def __init__(
            self,
            hass,
            logger: logging.Logger,
            *,
            config_entry: Any = None,
            name: str = "",
            update_interval: Any = None,
            update_method: Callable[[], Coroutine] | None = None,
            setup_method: Any = None,
            request_refresh_debouncer: Any = None,
            always_update: bool = True,
        ) -> None:
            self.hass = hass
            self.logger = logger
            self.name = name
            self.update_interval = update_interval
            self.update_method = update_method
            self.always_update = always_update
            self.config_entry = config_entry
            self.data: Any = None
            self.last_update_success = True
            self.last_exception: BaseException | None = None
            self.shutdown_requested = False

            #: Test observability - distinguishes async_refresh from the
            #: debounced async_request_refresh.
            self.refresh_calls = 0
            self.request_refresh_calls = 0

            self._listeners: dict[int, tuple[Callable, Any]] = {}
            self._next_listener_id = 0

        def async_add_listener(self, update_callback: Callable, context: Any = None):
            self._next_listener_id += 1
            key = self._next_listener_id
            self._listeners[key] = (update_callback, context)
            return lambda: self._listeners.pop(key, None)

        def async_update_listeners(self) -> None:
            for update_callback, _ in list(self._listeners.values()):
                update_callback()

        async def async_shutdown(self) -> None:
            self.shutdown_requested = True

        async def _async_update_data(self):
            if self.update_method is None:
                raise NotImplementedError("Update method not implemented")
            return await self.update_method()

        async def async_config_entry_first_refresh(self) -> None:
            await self._refresh(raise_on_entry_error=True)
            if self.last_update_success:
                return
            error = ConfigEntryNotReady()
            error.__cause__ = self.last_exception
            raise error

        async def async_refresh(self) -> None:
            self.refresh_calls += 1
            await self._refresh()

        async def async_request_refresh(self) -> None:
            self.request_refresh_calls += 1
            await self._refresh()

        async def _refresh(self, raise_on_entry_error: bool = False) -> None:
            previously_successful = self.last_update_success
            try:
                self.data = await self._async_update_data()
            except UpdateFailed as err:
                self.last_exception = err
                if self.last_update_success:
                    self.logger.error("Error fetching %s data: %s", self.name, err)
                    self.last_update_success = False
            except (ConfigEntryError, ConfigEntryAuthFailed) as err:
                self.last_exception = err
                self.last_update_success = False
                if raise_on_entry_error:
                    raise
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001
                self.last_exception = err
                self.last_update_success = False
            else:
                if not self.last_update_success:
                    self.last_update_success = True

            if not self.last_update_success and not previously_successful:
                return
            self.async_update_listeners()

    class BaseCoordinatorEntity(Entity):
        def __init__(self, coordinator, context: Any = None) -> None:
            self.coordinator = coordinator
            self.coordinator_context = context

        @property
        def should_poll(self) -> bool:
            return False

    class CoordinatorEntity(BaseCoordinatorEntity):
        def __class_getitem__(cls, item):
            return cls

        @property
        def available(self) -> bool:
            return self.coordinator.last_update_success

        def _handle_coordinator_update(self) -> None:
            self.async_write_ha_state()

        async def async_added_to_hass(self) -> None:
            self.coordinator.async_add_listener(
                self._handle_coordinator_update, self.coordinator_context
            )

        async def async_update(self) -> None:
            await self.coordinator.async_request_refresh()

    coordinator_module.DataUpdateCoordinator = DataUpdateCoordinator
    coordinator_module.BaseCoordinatorEntity = BaseCoordinatorEntity
    coordinator_module.CoordinatorEntity = CoordinatorEntity
    coordinator_module.UpdateFailed = UpdateFailed

    # ------------------------------------------------------------ platforms
    _module("homeassistant.components")

    climate = _module("homeassistant.components.climate")
    climate_const = _module("homeassistant.components.climate.const")

    class HVACMode(str):
        OFF = "off"
        HEAT = "heat"
        COOL = "cool"
        AUTO = "auto"
        FAN_ONLY = "fan_only"
        HEAT_COOL = "heat_cool"

    class HVACAction(str):
        OFF = "off"
        HEATING = "heating"
        COOLING = "cooling"
        IDLE = "idle"
        FAN = "fan"

    class ClimateEntityFeature:
        TARGET_TEMPERATURE = 1
        TARGET_TEMPERATURE_RANGE = 2
        FAN_MODE = 8
        PRESET_MODE = 16
        TURN_OFF = 128
        TURN_ON = 256

    class ClimateEntity(Entity):
        _attr_temperature_unit: str | None = None

        @property
        def temperature_unit(self):
            return self._attr_temperature_unit

        @property
        def hvac_modes(self):
            return []

        @property
        def fan_modes(self):
            return []

        async def async_set_hvac_mode(self, hvac_mode):
            raise NotImplementedError

        async def async_turn_on(self) -> None:
            """Reproduces core's fallback, which tests assert is overridden."""
            if len(self.hvac_modes) == 2 and HVACMode.OFF in self.hvac_modes:
                for mode in self.hvac_modes:
                    if mode != HVACMode.OFF:
                        await self.async_set_hvac_mode(mode)
                        return
            for mode in (HVACMode.HEAT_COOL, HVACMode.HEAT, HVACMode.COOL):
                if mode in self.hvac_modes:
                    await self.async_set_hvac_mode(mode)
                    return
            raise NotImplementedError

        async def async_turn_off(self) -> None:
            if HVACMode.OFF in self.hvac_modes:
                await self.async_set_hvac_mode(HVACMode.OFF)
                return
            raise NotImplementedError

    climate.ClimateEntity = ClimateEntity
    climate.ClimateEntityFeature = ClimateEntityFeature
    climate.HVACAction = HVACAction
    climate.HVACMode = HVACMode

    climate_const.FAN_OFF = "off"
    climate_const.FAN_LOW = "low"
    climate_const.FAN_MEDIUM = "medium"
    climate_const.FAN_HIGH = "high"
    climate_const.HVACMode = HVACMode
    climate_const.HVACAction = HVACAction
    climate_const.ClimateEntityFeature = ClimateEntityFeature
    for _name in ("FAN_OFF", "FAN_LOW", "FAN_MEDIUM", "FAN_HIGH"):
        setattr(climate, _name, getattr(climate_const, _name))

    switch = _module("homeassistant.components.switch")

    class SwitchEntity(Entity):
        @property
        def is_on(self):
            return None

        async def async_turn_on(self, **kwargs):
            raise NotImplementedError

        async def async_turn_off(self, **kwargs):
            raise NotImplementedError

    switch.SwitchEntity = SwitchEntity

    button = _module("homeassistant.components.button")

    class ButtonEntity(Entity):
        async def async_press(self) -> None:
            raise NotImplementedError

    button.ButtonEntity = ButtonEntity

    select = _module("homeassistant.components.select")

    class SelectEntity(Entity):
        _attr_options: list[str] = []
        _attr_current_option: str | None = None

        @property
        def options(self) -> list[str]:
            return self._attr_options

        @property
        def current_option(self) -> str | None:
            return self._attr_current_option

        async def async_select_option(self, option: str) -> None:
            raise NotImplementedError

    select.SelectEntity = SelectEntity

    # sensor
    sensor = _module("homeassistant.components.sensor")

    class SensorDeviceClass(str):
        TEMPERATURE = "temperature"
        HUMIDITY = "humidity"
        PRESSURE = "pressure"
        DURATION = "duration"
        FREQUENCY = "frequency"
        POWER = "power"
        ENERGY = "energy"

    class SensorStateClass(str):
        MEASUREMENT = "measurement"
        TOTAL = "total"
        TOTAL_INCREASING = "total_increasing"

    @dataclass(frozen=True, kw_only=True)
    class EntityDescription:
        key: str
        device_class: Any = None
        entity_category: Any = None
        entity_registry_enabled_default: bool = True
        has_entity_name: bool = False
        icon: str | None = None
        name: Any = UNDEFINED
        translation_key: str | None = None
        unit_of_measurement: str | None = None

    @dataclass(frozen=True, kw_only=True)
    class SensorEntityDescription(EntityDescription):
        native_unit_of_measurement: str | None = None
        state_class: Any = None
        suggested_display_precision: int | None = None
        suggested_unit_of_measurement: str | None = None
        last_reset: Any = None
        options: Any = None

    class SensorEntity(Entity):
        _attr_native_value: Any = None

        @property
        def device_class(self):
            return self._described("device_class")

        @property
        def state_class(self):
            return self._described("state_class")

        @property
        def native_unit_of_measurement(self):
            return self._described("native_unit_of_measurement")

        @property
        def suggested_display_precision(self):
            return self._described("suggested_display_precision")

        @property
        def native_value(self):
            return self._attr_native_value

        @property
        def state(self):
            return self.native_value

    sensor.SensorDeviceClass = SensorDeviceClass
    sensor.SensorStateClass = SensorStateClass
    sensor.SensorEntity = SensorEntity
    sensor.SensorEntityDescription = SensorEntityDescription
    entity_module.EntityDescription = EntityDescription

    # number
    number = _module("homeassistant.components.number")

    class NumberMode(str):
        AUTO = "auto"
        BOX = "box"
        SLIDER = "slider"

    @dataclass(frozen=True, kw_only=True)
    class NumberEntityDescription(EntityDescription):
        native_max_value: float | None = None
        native_min_value: float | None = None
        native_step: float | None = None
        native_unit_of_measurement: str | None = None
        mode: Any = None

    class NumberEntity(Entity):
        _attr_native_value: float | None = None
        _attr_native_min_value: float = 0
        _attr_native_max_value: float = 100
        _attr_native_step: float = 1
        _attr_mode: Any = NumberMode.AUTO

        @property
        def native_min_value(self) -> float:
            return self._described("native_min_value", 0)

        @property
        def native_max_value(self) -> float:
            return self._described("native_max_value", 100)

        @property
        def native_step(self) -> float:
            return self._described("native_step", 1)

        @property
        def native_unit_of_measurement(self):
            return self._described("native_unit_of_measurement")

        @property
        def mode(self):
            return self._described("mode")

        @property
        def native_value(self) -> float | None:
            return self._attr_native_value

        async def async_set_native_value(self, value: float) -> None:
            raise NotImplementedError

        async def async_set_value(self, value: float) -> None:
            """Mirror core's range validation before delegating."""
            if not self.native_min_value <= value <= self.native_max_value:
                raise ServiceValidationError(
                    f"{value} is outside valid range "
                    f"{self.native_min_value}-{self.native_max_value}"
                )
            await self.async_set_native_value(value)

    number.NumberEntity = NumberEntity
    number.NumberEntityDescription = NumberEntityDescription
    number.NumberMode = NumberMode

    # binary_sensor
    binary_sensor = _module("homeassistant.components.binary_sensor")

    class BinarySensorDeviceClass(str):
        PROBLEM = "problem"
        SAFETY = "safety"
        RUNNING = "running"
        CONNECTIVITY = "connectivity"

    @dataclass(frozen=True, kw_only=True)
    class BinarySensorEntityDescription(EntityDescription):
        pass

    class BinarySensorEntity(Entity):
        _attr_is_on: bool | None = None

        @property
        def device_class(self):
            return self._described("device_class")

        @property
        def is_on(self) -> bool | None:
            return self._attr_is_on

        @property
        def state(self):
            if self.is_on is None:
                return None
            return "on" if self.is_on else "off"

    binary_sensor.BinarySensorDeviceClass = BinarySensorDeviceClass
    binary_sensor.BinarySensorEntity = BinarySensorEntity
    binary_sensor.BinarySensorEntityDescription = BinarySensorEntityDescription
