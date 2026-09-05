"""Tests for the integration itself: setup, coordinator and entities.

Run against the fake S21 (:mod:`tests.fake_s21`) with the Home Assistant API
stub (:mod:`tests.hass_stub`), so no hardware and no Home Assistant install are
needed.
"""
from __future__ import annotations

import asyncio
import contextlib

import pytest

from tests.conftest import requires_hass_stub
from tests.fake_s21 import Fault

pytestmark = [pytest.mark.asyncio, requires_hass_stub]


def all_platforms():
    """Every platform module, in the order Home Assistant sets them up."""
    from blauberg_s21_ext import (
        button as button_platform,
    )
    from blauberg_s21_ext import (
        climate as climate_platform,
    )
    from blauberg_s21_ext import (
        number as number_platform,
    )
    from blauberg_s21_ext import (
        select as select_platform,
    )
    from blauberg_s21_ext import (
        sensor as sensor_platform,
    )
    from blauberg_s21_ext import (
        switch as switch_platform,
    )

    return (
        climate_platform,
        switch_platform,
        button_platform,
        select_platform,
        sensor_platform,
        number_platform,
    )


# ---------------------------------------------------------------- helpers
async def setup_integration(hass, entry):
    """Run async_setup_entry the way Home Assistant would."""
    import blauberg_s21_ext as integration
    from homeassistant.config_entries import ConfigEntryState

    result = await integration.async_setup_entry(hass, entry)
    entry.state = ConfigEntryState.LOADED
    return result


async def teardown_integration(hass, entry):
    import blauberg_s21_ext as integration

    unloaded = await integration.async_unload_entry(hass, entry)
    await entry.run_on_unload()
    return unloaded


@pytest.fixture
async def loaded(hass_stub_hass, config_entry_factory, fake_server):
    """A fully set up integration wired to the fake server."""
    import blauberg_s21_ext as integration

    host, port = fake_server.address
    entry = config_entry_factory(host, port)
    await setup_integration(hass_stub_hass, entry)
    data = integration.get_data(hass_stub_hass, entry)
    try:
        yield hass_stub_hass, entry, data
    finally:
        # Teardown must never mask the failure a test is reporting.
        with contextlib.suppress(Exception):
            await teardown_integration(hass_stub_hass, entry)


# ---------------------------------------------------------------- lifecycle
async def test_setup_stores_runtime_data_and_forwards_platforms(loaded):
    from blauberg_s21_ext.const import DOMAIN

    hass, entry, data = loaded

    assert entry.entry_id in hass.data[DOMAIN]
    assert data.client is not None
    assert data.coordinator is not None
    assert data.coordinator.data is not None

    forwarded = hass.config_entries.forwarded
    assert len(forwarded) == 1
    assert set(forwarded[0][1]) == {
        "climate",
        "button",
        "number",
        "select",
        "sensor",
        "switch",
    }


async def test_unload_closes_the_socket_and_cleans_up(
    hass_stub_hass, config_entry_factory, fake_server
):
    import blauberg_s21_ext as integration
    from blauberg_s21_ext.const import DOMAIN

    host, port = fake_server.address
    entry = config_entry_factory(host, port)
    await setup_integration(hass_stub_hass, entry)
    data = integration.get_data(hass_stub_hass, entry)

    assert await teardown_integration(hass_stub_hass, entry) is True

    assert DOMAIN not in hass_stub_hass.data
    assert data.client._closed is True
    await fake_server.wait_idle()


async def test_unload_leaves_data_in_place_when_platforms_refuse(
    hass_stub_hass, config_entry_factory, fake_server
):
    import blauberg_s21_ext as integration
    from blauberg_s21_ext.const import DOMAIN

    host, port = fake_server.address
    entry = config_entry_factory(host, port)
    await setup_integration(hass_stub_hass, entry)

    hass_stub_hass.config_entries.unload_result = False
    assert await integration.async_unload_entry(hass_stub_hass, entry) is False
    assert entry.entry_id in hass_stub_hass.data[DOMAIN]

    hass_stub_hass.config_entries.unload_result = True
    await teardown_integration(hass_stub_hass, entry)
    assert DOMAIN not in hass_stub_hass.data


async def test_setup_raises_config_entry_not_ready_when_unreachable(
    hass_stub_hass, config_entry_factory, closed_port
):
    import blauberg_s21_ext as integration
    from blauberg_s21_ext.const import DOMAIN
    from homeassistant.exceptions import ConfigEntryNotReady

    host, port = closed_port
    entry = config_entry_factory(host, port)

    with pytest.raises(ConfigEntryNotReady):
        await integration.async_setup_entry(hass_stub_hass, entry)

    assert not hass_stub_hass.data.get(DOMAIN)
    # Home Assistant runs the on-unload callbacks after a failed setup, which
    # must release the socket rather than leave the unit's only slot reserved.
    await entry.run_on_unload()


async def test_unsupported_device_is_a_hard_error_not_a_retry(
    hass_stub_hass, config_entry_factory, fake_server
):
    """An unsupported device is permanent, so Home Assistant must not retry.

    ConfigEntryNotReady would make it retry with backoff forever;
    ConfigEntryError stops and surfaces the problem to the user.
    """
    import blauberg_s21_ext as integration
    from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady

    fake_server.fault = Fault.WRONG_DEVICE_TYPE
    host, port = fake_server.address
    entry = config_entry_factory(host, port)

    with pytest.raises(ConfigEntryError) as info:
        await integration.async_setup_entry(hass_stub_hass, entry)

    assert not isinstance(info.value, ConfigEntryNotReady)
    assert "Blauberg S21" in str(info.value)
    await entry.run_on_unload()
    await fake_server.wait_idle()


# -------------------------------------------------- shutdown hook (the fix)
async def test_stop_event_closes_the_socket(loaded, fake_server):
    """Regression: Home Assistant never unloads entries on shutdown.

    On EVENT_HOMEASSISTANT_STOP it only cancels pending setup retries, so
    async_unload_entry is not called. Without an explicit listener the Modbus
    socket stayed open and the unit kept its only connection slot reserved,
    which is what made the entities come back unavailable after a restart.
    """
    from homeassistant.const import EVENT_HOMEASSISTANT_STOP

    hass, _entry, data = loaded
    assert data.client._closed is False
    assert EVENT_HOMEASSISTANT_STOP in hass.bus.listeners

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)

    assert data.client._closed is True
    await fake_server.wait_idle()


async def test_restart_after_stop_event_connects_immediately(
    hass_stub_hass, config_entry_factory, fake_server
):
    from homeassistant.const import EVENT_HOMEASSISTANT_STOP

    host, port = fake_server.address

    first_entry = config_entry_factory(host, port)
    await setup_integration(hass_stub_hass, first_entry)
    hass_stub_hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await fake_server.wait_idle()

    # A brand new HomeAssistant, as after a restart.
    from homeassistant.core import HomeAssistant

    restarted = HomeAssistant()
    second_entry = config_entry_factory(host, port)
    try:
        assert await setup_integration(restarted, second_entry) is True
    finally:
        await teardown_integration(restarted, second_entry)


async def test_repeated_hard_restarts_all_succeed(
    config_entry_factory, fake_server
):
    """Only the stop hook runs each time, never async_unload_entry."""
    from homeassistant.const import EVENT_HOMEASSISTANT_STOP
    from homeassistant.core import HomeAssistant

    host, port = fake_server.address
    for _ in range(5):
        hass = HomeAssistant()
        entry = config_entry_factory(host, port)
        assert await setup_integration(hass, entry) is True
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
        await fake_server.wait_idle()


async def test_coordinator_is_shut_down_on_unload(loaded):
    hass, entry, data = loaded
    assert data.coordinator.shutdown_requested is False
    await teardown_integration(hass, entry)
    assert data.coordinator.shutdown_requested is True


# ------------------------------------------------------------ grace period
async def test_entities_survive_a_short_outage(loaded, fake_server, add_entities):
    import blauberg_s21_ext as integration
    from blauberg_s21_ext import climate as climate_platform

    hass, entry, data = loaded
    coordinator = data.coordinator
    climate = (await add_entities(climate_platform, hass, entry))[0]

    assert climate.available is True
    temperature = climate.current_temperature
    assert temperature is not None

    fake_server.fault = Fault.REFUSE_CONNECTIONS

    for poll in range(1, integration.FAILURE_GRACE_COUNT):
        await coordinator.async_refresh()
        assert coordinator.last_update_success is False
        assert climate.available is True, f"unavailable too early on poll {poll}"
        assert climate.current_temperature == temperature, "last state must persist"

    await coordinator.async_refresh()
    assert climate.available is False, "must give up after the grace period"

    await coordinator.async_refresh()
    assert climate.available is False

    fake_server.fault = Fault.NONE
    await coordinator.async_refresh()
    assert climate.available is True
    assert coordinator._consecutive_failures == 0


async def test_device_reachable_tracks_the_failure_count(loaded, fake_server):
    hass, entry, data = loaded
    coordinator = data.coordinator

    assert coordinator.device_reachable is True

    fake_server.fault = Fault.REFUSE_CONNECTIONS
    await coordinator.async_refresh()
    assert coordinator.device_reachable is True
    await coordinator.async_refresh()
    assert coordinator.device_reachable is True
    await coordinator.async_refresh()
    assert coordinator.device_reachable is False

    fake_server.fault = Fault.NONE
    await coordinator.async_refresh()
    assert coordinator.device_reachable is True


# ------------------------------------------------------------- unique ids
async def test_entity_unique_ids_are_unchanged(loaded, add_entities):
    """Existing installations must not get a second set of entities.

    The ids for the ported number and sensor entities deliberately match the
    ones the upstream implementations produced, for the same reason.
    """
    hass, entry, _data = loaded
    base = entry.unique_id

    expected = {
        base,
        f"{base}_boost_switch",
        f"{base}_timer_switch",
        f"{base}_schedule_mode_switch",
        f"{base}_reset_filter_button",
        f"{base}_reset_alarm_button",
        f"blauberg_s21_{base}_bypass_mode",
        f"{base}_manual_fan_speed",
        f"{base}_supply_outdoor_temperature",
        f"{base}_supply_temperature",
        f"{base}_extract_temperature",
        f"{base}_extract_outlet_temperature",
    }

    entities = []
    for platform in all_platforms():
        entities.extend(await add_entities(platform, hass, entry))

    found = {entity.unique_id for entity in entities}
    assert found == expected
    assert len(found) == len(entities), "unique ids must not collide"


async def test_all_entities_belong_to_one_device(loaded, add_entities):
    from blauberg_s21_ext.const import DOMAIN

    hass, entry, _data = loaded

    entities = []
    for platform in all_platforms():
        entities.extend(await add_entities(platform, hass, entry))

    identifiers = {
        tuple(sorted(entity.device_info["identifiers"])) for entity in entities
    }
    assert identifiers == {((DOMAIN, entry.unique_id),)}

    info = entities[0].device_info
    assert info["manufacturer"] == "Blauberg"
    assert info["model"] == "S21"
    assert info["sw_version"] == "1.9 (2024-06-28)"


async def test_entity_names_come_from_the_translations(loaded, add_entities):
    """`has_entity_name` plus a translation key, not a hardcoded _attr_name."""
    from blauberg_s21_ext import climate as climate_platform
    from homeassistant.helpers.entity import UNDEFINED

    hass, entry, _data = loaded

    climate = (await add_entities(climate_platform, hass, entry))[0]
    assert climate.has_entity_name is True
    # None means "this entity represents the device", so it inherits its name.
    assert climate.name is None

    others = []
    for platform in all_platforms():
        if platform is climate_platform:
            continue
        others.extend(await add_entities(platform, hass, entry))

    assert len(others) == 11
    for entity in others:
        assert entity.has_entity_name is True
        assert entity.translation_key is not None
        assert entity.name is not UNDEFINED, (
            f"{type(entity).__name__} has no translated name for "
            f"{entity.translation_key}"
        )
        assert entity.name


# ---------------------------------------------------------------- climate
async def test_climate_reports_device_state(loaded, add_entities):
    from blauberg_s21_ext import climate as climate_platform

    hass, entry, _data = loaded
    climate = (await add_entities(climate_platform, hass, entry))[0]

    assert climate.temperature_unit == "\u00b0C"
    assert climate.precision == 1
    assert (climate.min_temp, climate.max_temp) == (15, 30)
    assert climate.target_temperature_step == 1
    assert climate.current_temperature == 23.2
    assert climate.target_temperature == 20
    assert climate.current_humidity == 45
    assert climate.hvac_modes == ["off", "heat", "cool", "auto", "fan_only"]
    assert climate.fan_modes == ["low", "medium", "high", "custom"]
    assert climate.fan_mode == "medium"
    assert climate.icon == "mdi:fan-speed-2"


async def test_climate_declares_the_features_it_implements(loaded, add_entities):
    from blauberg_s21_ext import climate as climate_platform
    from homeassistant.components.climate import ClimateEntityFeature

    hass, entry, _data = loaded
    climate = (await add_entities(climate_platform, hass, entry))[0]

    features = climate.supported_features
    for flag in (
        ClimateEntityFeature.TARGET_TEMPERATURE,
        ClimateEntityFeature.FAN_MODE,
        ClimateEntityFeature.TURN_ON,
        ClimateEntityFeature.TURN_OFF,
    ):
        assert features & flag


async def test_climate_exposes_all_telemetry(loaded, add_entities):
    from blauberg_s21_ext import climate as climate_platform

    hass, entry, _data = loaded
    climate = (await add_entities(climate_platform, hass, entry))[0]
    attributes = climate.extra_state_attributes

    assert attributes["supply_fan_speed"] == 2460
    assert attributes["extract_fan_speed"] == 2520
    assert attributes["manual_fan_speed_percent"] == 50
    assert attributes["max_fan_level"] == 3
    assert attributes["current_intake_temperature_in"] == 21.0
    assert attributes["current_outlet_temperature_out"] == 15.0
    assert attributes["filter_countdown"] == 90
    assert attributes["timer_countdown"] == "00:00:00"
    assert attributes["alarm_codes"] == []
    assert attributes["bypass_mode"] == 2
    assert attributes["fan_level_manual_mode"] == "medium"


async def test_climate_turn_on_uses_the_power_coil(loaded, fake_server, add_entities):
    """Regression: TURN_ON was advertised without an implementation.

    Home Assistant's fallback picks HVACMode.HEAT, which switched the heater on
    instead of simply powering the unit up.
    """
    from blauberg_s21_ext import climate as climate_platform

    hass, entry, _data = loaded
    climate = (await add_entities(climate_platform, hass, entry))[0]

    fake_server.holding[43] = 2  # currently in cool mode
    fake_server.coils[0] = False
    fake_server.writes.clear()

    await climate.async_turn_on()

    assert ("coil", 0, 1) in fake_server.writes
    assert not [w for w in fake_server.writes if w[:2] == ("register", 43)], (
        "turning on must not change the operating mode"
    )
    assert fake_server.holding[43] == 2


async def test_climate_turn_off_uses_the_power_coil(loaded, fake_server, add_entities):
    from blauberg_s21_ext import climate as climate_platform

    hass, entry, _data = loaded
    climate = (await add_entities(climate_platform, hass, entry))[0]

    fake_server.writes.clear()
    await climate.async_turn_off()
    assert ("coil", 0, 0) in fake_server.writes


@pytest.mark.parametrize(
    ("fan_mode", "expected_level"),
    [("low", 1), ("medium", 2), ("high", 3), ("custom", 255), ("2", 2)],
)
async def test_climate_fan_mode_round_trip(
    loaded, fake_server, add_entities, fan_mode, expected_level
):
    from blauberg_s21_ext import climate as climate_platform

    hass, entry, _data = loaded
    climate = (await add_entities(climate_platform, hass, entry))[0]

    fake_server.writes.clear()
    await climate.async_set_fan_mode(fan_mode)
    assert ("register", 2, expected_level) in fake_server.writes


async def test_climate_uses_the_devices_real_fan_limit(
    hass_stub_hass, config_entry_factory, fake_server, add_entities
):
    """Regression: the write path hardcoded a maximum of three speeds."""
    from blauberg_s21_ext import climate as climate_platform

    fake_server.holding[1] = 8  # HR_MaxSPEED_MODE
    host, port = fake_server.address
    entry = config_entry_factory(host, port)
    await setup_integration(hass_stub_hass, entry)
    try:
        climate = (await add_entities(climate_platform, hass_stub_hass, entry))[0]
        assert climate.fan_modes == ["1", "2", "3", "4", "5", "6", "7", "8", "custom"]

        fake_server.writes.clear()
        await climate.async_set_fan_mode("7")
        assert ("register", 2, 7) in fake_server.writes
    finally:
        await teardown_integration(hass_stub_hass, entry)


async def test_climate_fan_mode_off_powers_the_unit_down(
    loaded, fake_server, add_entities
):
    from blauberg_s21_ext import climate as climate_platform

    hass, entry, _data = loaded
    climate = (await add_entities(climate_platform, hass, entry))[0]

    fake_server.writes.clear()
    await climate.async_set_fan_mode("off")
    assert ("coil", 0, 0) in fake_server.writes


@pytest.mark.parametrize("fan_mode", ["nonsense", "", "auto"])
async def test_climate_rejects_unknown_fan_modes(loaded, add_entities, fan_mode):
    from blauberg_s21_ext import climate as climate_platform
    from homeassistant.exceptions import ServiceValidationError

    hass, entry, _data = loaded
    climate = (await add_entities(climate_platform, hass, entry))[0]

    with pytest.raises(ServiceValidationError):
        await climate.async_set_fan_mode(fan_mode)


@pytest.mark.parametrize("temperature", [5, 14.4, 30.6, 99])
async def test_climate_rejects_out_of_range_temperatures(
    loaded, add_entities, temperature
):
    from blauberg_s21_ext import climate as climate_platform
    from homeassistant.exceptions import ServiceValidationError

    hass, entry, _data = loaded
    climate = (await add_entities(climate_platform, hass, entry))[0]

    with pytest.raises(ServiceValidationError):
        await climate.async_set_temperature(temperature=temperature)


@pytest.mark.parametrize(
    ("requested", "written"),
    [(21.6, 22), (21.4, 21), (20, 20), (29.5, 30)],
)
async def test_climate_rounds_the_target_temperature(
    loaded, fake_server, add_entities, requested, written
):
    """Regression: int() truncated, so 21.6 became 21."""
    from blauberg_s21_ext import climate as climate_platform

    hass, entry, _data = loaded
    climate = (await add_entities(climate_platform, hass, entry))[0]

    fake_server.writes.clear()
    await climate.async_set_temperature(temperature=requested)
    assert ("register", 44, written) in fake_server.writes


async def test_climate_set_temperature_without_a_value_does_nothing(
    loaded, fake_server, add_entities
):
    from blauberg_s21_ext import climate as climate_platform

    hass, entry, _data = loaded
    climate = (await add_entities(climate_platform, hass, entry))[0]

    fake_server.writes.clear()
    await climate.async_set_temperature()
    assert fake_server.writes == []


async def test_climate_hvac_mode_writes_power_and_mode(
    loaded, fake_server, add_entities
):
    from blauberg_s21_ext import climate as climate_platform

    hass, entry, _data = loaded
    climate = (await add_entities(climate_platform, hass, entry))[0]

    fake_server.writes.clear()
    await climate.async_set_hvac_mode("cool")
    assert ("coil", 0, 1) in fake_server.writes
    assert ("register", 43, 2) in fake_server.writes

    fake_server.writes.clear()
    await climate.async_set_hvac_mode("off")
    assert ("coil", 0, 0) in fake_server.writes


async def test_climate_rejects_unsupported_hvac_modes(loaded, add_entities):
    from blauberg_s21_ext import climate as climate_platform
    from homeassistant.exceptions import ServiceValidationError

    hass, entry, _data = loaded
    climate = (await add_entities(climate_platform, hass, entry))[0]

    with pytest.raises(ServiceValidationError):
        await climate.async_set_hvac_mode("dry")


async def test_climate_registers_its_entity_services(loaded, add_entities):
    from blauberg_s21_ext import climate as climate_platform
    from homeassistant.helpers.entity_platform import current_platform

    hass, entry, _data = loaded
    current_platform.services.clear()
    await add_entities(climate_platform, hass, entry)

    names = {name for name, _schema, _func in current_platform.services}
    assert {"reset_filter_change_timer", "reset_alarm"} <= names


async def test_climate_service_methods_write_their_coils(
    loaded, fake_server, add_entities
):
    from blauberg_s21_ext import climate as climate_platform

    hass, entry, _data = loaded
    climate = (await add_entities(climate_platform, hass, entry))[0]

    fake_server.writes.clear()
    await climate.async_reset_filter_change_timer()
    assert ("coil", 17, 1) in fake_server.writes

    fake_server.writes.clear()
    await climate.async_reset_alarm()
    assert ("coil", 18, 1) in fake_server.writes


async def test_climate_icon_reflects_state(loaded, fake_server, add_entities):
    from blauberg_s21_ext import climate as climate_platform

    hass, entry, data = loaded
    climate = (await add_entities(climate_platform, hass, entry))[0]

    fake_server.coils[3] = True  # boost status
    await data.coordinator.async_refresh()
    assert climate.icon == "mdi:fan-plus"

    fake_server.coils[3] = False
    fake_server.coils[0] = False  # powered off
    await data.coordinator.async_refresh()
    assert climate.icon == "mdi:fan-off"

    fake_server.coils[0] = True
    fake_server.fault = Fault.REFUSE_CONNECTIONS
    for _ in range(4):
        await data.coordinator.async_refresh()
    assert climate.icon == "mdi:lan-disconnect"


# ---------------------------------------------------------------- commands
async def test_commands_refresh_immediately(loaded, add_entities):
    """A command must not wait for the coordinator's 10 s debounce."""
    from blauberg_s21_ext import switch as switch_platform

    hass, entry, data = loaded
    boost = (await add_entities(switch_platform, hass, entry))[0]

    before_refresh = data.coordinator.refresh_calls
    before_debounced = data.coordinator.request_refresh_calls

    await boost.async_turn_on()

    assert data.coordinator.refresh_calls == before_refresh + 1
    assert data.coordinator.request_refresh_calls == before_debounced
    assert boost.is_on is True


async def test_communication_failures_become_home_assistant_errors(
    loaded, fake_server, add_entities
):
    from blauberg_s21_ext import switch as switch_platform
    from homeassistant.exceptions import HomeAssistantError

    hass, entry, _data = loaded
    boost = (await add_entities(switch_platform, hass, entry))[0]

    fake_server.fault = Fault.REFUSE_CONNECTIONS
    with pytest.raises(HomeAssistantError, match="Could not reach"):
        await boost.async_turn_on()


async def test_a_failed_command_does_not_pay_the_retry_cost_twice(
    loaded, fake_server, add_entities
):
    from blauberg_s21_ext import switch as switch_platform
    from homeassistant.exceptions import HomeAssistantError

    hass, entry, data = loaded
    boost = (await add_entities(switch_platform, hass, entry))[0]

    fake_server.fault = Fault.REFUSE_CONNECTIONS
    before = data.coordinator.refresh_calls
    with pytest.raises(HomeAssistantError):
        await boost.async_turn_on()
    assert data.coordinator.refresh_calls == before, (
        "a failed command must not also run a doomed refresh"
    )


# ------------------------------------------------------- switch and button
async def test_switches_mirror_the_device(loaded, fake_server, add_entities):
    from blauberg_s21_ext import switch as switch_platform

    hass, entry, data = loaded
    boost, timer, schedule = await add_entities(switch_platform, hass, entry)

    assert (boost.is_on, timer.is_on, schedule.is_on) == (False, False, False)

    fake_server.coils[3] = True
    fake_server.coils[1] = True
    fake_server.coils[2] = True
    await data.coordinator.async_refresh()

    assert (boost.is_on, timer.is_on, schedule.is_on) == (True, True, True)


@pytest.mark.parametrize(
    ("index", "on_write", "off_write"),
    [(0, ("coil", 13, 1), ("coil", 13, 0)),
     (1, ("coil", 1, 1), ("coil", 1, 0)),
     (2, ("coil", 2, 1), ("coil", 2, 0))],
)
async def test_switches_write_their_coils(
    loaded, fake_server, add_entities, index, on_write, off_write
):
    from blauberg_s21_ext import switch as switch_platform

    hass, entry, _data = loaded
    entity = (await add_entities(switch_platform, hass, entry))[index]

    fake_server.writes.clear()
    await entity.async_turn_on()
    assert on_write in fake_server.writes

    fake_server.writes.clear()
    await entity.async_turn_off()
    assert off_write in fake_server.writes


async def test_buttons_write_their_coils(loaded, fake_server, add_entities):
    from blauberg_s21_ext import button as button_platform

    hass, entry, _data = loaded
    reset_filter, reset_alarm = await add_entities(button_platform, hass, entry)

    fake_server.writes.clear()
    await reset_filter.async_press()
    assert ("coil", 17, 1) in fake_server.writes

    fake_server.writes.clear()
    await reset_alarm.async_press()
    assert ("coil", 18, 1) in fake_server.writes


# ----------------------------------------------------------------- select
async def test_bypass_select_reports_the_current_mode(
    loaded, fake_server, add_entities
):
    from blauberg_s21_ext import select as select_platform

    hass, entry, data = loaded
    selects = await add_entities(select_platform, hass, entry)
    assert len(selects) == 1
    bypass = selects[0]

    assert bypass.options == ["close", "open", "auto"]
    assert bypass.current_option == "auto"

    fake_server.holding[74] = 0
    await data.coordinator.async_refresh()
    assert bypass.current_option == "close"


@pytest.mark.parametrize("option", ["close", "open", "auto"])
async def test_bypass_select_writes_the_register(
    loaded, fake_server, add_entities, option
):
    from blauberg_s21_ext import select as select_platform

    hass, entry, _data = loaded
    bypass = (await add_entities(select_platform, hass, entry))[0]

    fake_server.writes.clear()
    await bypass.async_select_option(option)
    assert ("register", 74, ["close", "open", "auto"].index(option)) in (
        fake_server.writes
    )


@pytest.mark.parametrize("raw", [3, 7, 99, 65535])
async def test_bypass_select_survives_unexpected_register_values(
    loaded, fake_server, add_entities, raw
):
    """Regression: indexing the option list raised IndexError."""
    from blauberg_s21_ext import select as select_platform

    hass, entry, data = loaded
    bypass = (await add_entities(select_platform, hass, entry))[0]

    fake_server.holding[74] = raw
    await data.coordinator.async_refresh()
    assert bypass.current_option is None


async def test_bypass_select_is_not_created_without_a_bypass(
    hass_stub_hass, config_entry_factory, fake_server, add_entities
):
    from blauberg_s21_ext import select as select_platform

    fake_server.holding[57] = 0  # HR_BYPASS_ROTOR_TYPE, 0 = not fitted
    host, port = fake_server.address
    entry = config_entry_factory(host, port)
    await setup_integration(hass_stub_hass, entry)
    try:
        assert await add_entities(select_platform, hass_stub_hass, entry) == []
    finally:
        await teardown_integration(hass_stub_hass, entry)


# ------------------------------------------------------- temperature sensors
async def test_temperature_sensors_are_created(loaded, add_entities):
    from blauberg_s21_ext import sensor as sensor_platform

    hass, entry, _data = loaded
    sensors = await add_entities(sensor_platform, hass, entry)

    assert len(sensors) == 4
    assert {sensor.entity_description.key for sensor in sensors} == {
        "supply_outdoor_temperature",
        "supply_temperature",
        "extract_temperature",
        "extract_outlet_temperature",
    }


async def test_temperature_sensors_map_to_the_right_registers(loaded, add_entities):
    """The upstream commit targeted a pybls21 with different property names.

    Each sensor must land on the register it is named after; getting these
    crossed would silently report the wrong air stream.
    """
    from blauberg_s21_ext import sensor as sensor_platform

    hass, entry, data = loaded
    fake = None  # values come from the fake server's defaults

    sensors = {
        sensor.entity_description.key: sensor
        for sensor in await add_entities(sensor_platform, hass, entry)
    }
    del fake

    # Fake server defaults: SuAirIn 21.0, SuAirOut 23.2, ExAirIn 22.0, ExAirOut 15.0
    assert sensors["supply_outdoor_temperature"].native_value == 21.0
    assert sensors["supply_temperature"].native_value == 23.2
    assert sensors["extract_temperature"].native_value == 22.0
    assert sensors["extract_outlet_temperature"].native_value == 15.0

    # And they must track the device, not a snapshot.
    assert data.coordinator.data is not None


@pytest.mark.parametrize(
    ("key", "register", "raw", "expected"),
    [
        ("supply_outdoor_temperature", 1, 55, 5.5),
        ("supply_temperature", 2, 201, 20.1),
        ("extract_temperature", 3, 0x10000 - 35, -3.5),
        ("extract_outlet_temperature", 4, 0x10000 - 155, -15.5),
    ],
)
async def test_temperature_sensors_follow_their_register(
    loaded, fake_server, add_entities, key, register, raw, expected
):
    from blauberg_s21_ext import sensor as sensor_platform

    hass, entry, data = loaded
    sensors = {
        sensor.entity_description.key: sensor
        for sensor in await add_entities(sensor_platform, hass, entry)
    }

    fake_server.inputs[register] = raw
    await data.coordinator.async_refresh()
    assert sensors[key].native_value == expected


async def test_temperature_sensors_are_statistics_ready(loaded, add_entities):
    """device_class plus state_class is what gives long-term statistics."""
    from blauberg_s21_ext import sensor as sensor_platform

    hass, entry, _data = loaded

    for sensor in await add_entities(sensor_platform, hass, entry):
        assert sensor.device_class == "temperature"
        assert sensor.state_class == "measurement"
        assert sensor.native_unit_of_measurement == "\u00b0C"
        assert sensor.suggested_display_precision == 1


async def test_temperature_sensors_go_unavailable_with_the_device(
    loaded, fake_server, add_entities
):
    from blauberg_s21_ext import sensor as sensor_platform

    hass, entry, data = loaded
    sensors = await add_entities(sensor_platform, hass, entry)
    assert all(sensor.available for sensor in sensors)

    fake_server.fault = Fault.REFUSE_CONNECTIONS
    for _ in range(4):
        await data.coordinator.async_refresh()
    assert not any(sensor.available for sensor in sensors)


# ----------------------------------------------------- manual fan speed slider
async def test_manual_fan_speed_number_is_a_percentage_slider(loaded, add_entities):
    from blauberg_s21_ext import number as number_platform

    hass, entry, _data = loaded
    numbers = await add_entities(number_platform, hass, entry)

    assert len(numbers) == 1
    slider = numbers[0]
    assert slider.mode == "slider"
    assert (slider.native_min_value, slider.native_max_value) == (0, 100)
    assert slider.native_step == 1
    assert slider.native_unit_of_measurement == "%"
    # Fake server default for HR_ManualSPEED
    assert slider.native_value == 50


async def test_manual_fan_speed_writes_the_register(loaded, fake_server, add_entities):
    from blauberg_s21_ext import number as number_platform

    hass, entry, _data = loaded
    slider = (await add_entities(number_platform, hass, entry))[0]

    fake_server.writes.clear()
    await slider.async_set_native_value(75)

    assert ("register", 17, 75) in fake_server.writes
    assert slider.native_value == 75


@pytest.mark.parametrize(
    ("requested", "written"),
    [(0, 0), (100, 100), (33.4, 33), (33.6, 34), (99.5, 100)],
)
async def test_manual_fan_speed_rounds_rather_than_truncates(
    loaded, fake_server, add_entities, requested, written
):
    from blauberg_s21_ext import number as number_platform

    hass, entry, _data = loaded
    slider = (await add_entities(number_platform, hass, entry))[0]

    fake_server.writes.clear()
    await slider.async_set_native_value(requested)
    assert ("register", 17, written) in fake_server.writes


@pytest.mark.parametrize("value", [-1, 101, 250])
async def test_manual_fan_speed_rejects_out_of_range_values(
    loaded, fake_server, add_entities, value
):
    """Home Assistant range-checks before calling us; the client also validates."""
    from blauberg_s21_ext import number as number_platform
    from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

    hass, entry, _data = loaded
    slider = (await add_entities(number_platform, hass, entry))[0]

    fake_server.writes.clear()
    with pytest.raises((ServiceValidationError, HomeAssistantError)):
        await slider.async_set_value(value)
    assert fake_server.writes == [], "nothing should reach the device"


async def test_manual_fan_speed_reports_whether_it_is_in_effect(
    loaded, fake_server, add_entities
):
    """The value only drives the unit while the fan mode is custom (255)."""
    from blauberg_s21_ext import number as number_platform

    hass, entry, data = loaded
    slider = (await add_entities(number_platform, hass, entry))[0]

    assert slider.extra_state_attributes["active"] is False
    assert slider.extra_state_attributes["fan_mode_required"] == "custom"

    fake_server.holding[2] = 255  # HR_SPEED_MODE -> manual
    await data.coordinator.async_refresh()
    assert slider.extra_state_attributes["active"] is True


async def test_manual_fan_speed_survives_a_custom_mode_round_trip(
    loaded, fake_server, add_entities
):
    """Setting the climate fan mode to custom must not clear the percentage."""
    from blauberg_s21_ext import climate as climate_platform
    from blauberg_s21_ext import number as number_platform

    hass, entry, data = loaded
    slider = (await add_entities(number_platform, hass, entry))[0]
    climate = (await add_entities(climate_platform, hass, entry))[0]

    await slider.async_set_native_value(42)
    await climate.async_set_fan_mode("custom")
    await data.coordinator.async_refresh()

    assert climate.fan_mode == "custom"
    assert slider.native_value == 42
    assert slider.extra_state_attributes["active"] is True


# ------------------------------------------------------------- config flow
async def test_config_flow_rejects_an_unreachable_host(hass_stub_hass, closed_port):
    from blauberg_s21_ext.config_flow import CannotConnect, validate_input
    from homeassistant.const import CONF_HOST, CONF_PORT

    host, port = closed_port
    with pytest.raises(CannotConnect):
        await validate_input(hass_stub_hass, {CONF_HOST: host, CONF_PORT: port})


async def test_config_flow_accepts_the_device(hass_stub_hass, fake_server):
    from blauberg_s21_ext.config_flow import validate_input
    from homeassistant.const import CONF_HOST, CONF_PORT

    host, port = fake_server.address
    info = await validate_input(hass_stub_hass, {CONF_HOST: host, CONF_PORT: port})

    assert info["title"] == "Blauberg S21"
    assert info["unique_id"] == f"S21_{host}_{port}"
    await fake_server.wait_idle()


async def test_config_flow_rejects_an_unsupported_device(hass_stub_hass, fake_server):
    from blauberg_s21_ext.config_flow import validate_input
    from blauberg_s21_ext.pybls21.exceptions import UnsupportedDeviceException
    from homeassistant.const import CONF_HOST, CONF_PORT

    fake_server.fault = Fault.WRONG_DEVICE_TYPE
    host, port = fake_server.address
    with pytest.raises(UnsupportedDeviceException):
        await validate_input(hass_stub_hass, {CONF_HOST: host, CONF_PORT: port})
    await fake_server.wait_idle()


async def test_config_flow_closes_its_socket(hass_stub_hass, fake_server):
    """The flow must not hold the unit's only slot after validating."""
    from blauberg_s21_ext.config_flow import validate_input
    from homeassistant.const import CONF_HOST, CONF_PORT

    host, port = fake_server.address
    await validate_input(hass_stub_hass, {CONF_HOST: host, CONF_PORT: port})
    await fake_server.wait_idle()
    assert fake_server.active_connections == 0


# ------------------------------------------------------------ housekeeping
async def test_no_asyncio_tasks_are_leaked_by_a_full_cycle(
    hass_stub_hass, config_entry_factory, fake_server, pending_tasks
):
    before = len(pending_tasks())

    host, port = fake_server.address
    entry = config_entry_factory(host, port)
    await setup_integration(hass_stub_hass, entry)
    await teardown_integration(hass_stub_hass, entry)
    await asyncio.sleep(0.05)

    assert len(pending_tasks()) <= before


async def test_unroutable_host_fails_without_hanging(
    hass_stub_hass, config_entry_factory, unroutable_target
):
    """A host that never answers must still fail inside a bounded time."""
    import blauberg_s21_ext as integration
    from homeassistant.exceptions import ConfigEntryNotReady

    host, port = unroutable_target
    entry = config_entry_factory(host, port)

    loop = asyncio.get_running_loop()
    started = loop.time()
    with pytest.raises(ConfigEntryNotReady):
        await integration.async_setup_entry(hass_stub_hass, entry)
    assert loop.time() - started < 40
    await entry.run_on_unload()
