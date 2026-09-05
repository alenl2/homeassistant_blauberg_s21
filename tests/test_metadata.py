"""Offline checks on the integration's metadata, services and translations.

These need neither hardware nor Home Assistant, so they are the cheapest guard
against the kind of mistake that only shows up as a missing label in the UI.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

DOMAIN = "blauberg_s21_ext"

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPONENT = REPO_ROOT / "custom_components" / DOMAIN
LANGUAGE_FILES = sorted((COMPONENT / "translations").glob("*.json"))
LANGUAGE_IDS = [path.stem for path in LANGUAGE_FILES]

#: Keys that Home Assistant serves from translations/ at runtime. strings.json is
#: only used by core's own tooling, so a custom integration that lists a key
#: there but not in translations/en.json silently shows nothing.
REQUIRED_KEYS = (
    "config.step.user.data.host",
    "config.step.user.data.port",
    "config.step.reconfigure.data.host",
    "config.step.reconfigure.data.port",
    "config.error.cannot_connect",
    "config.error.unknown",
    "config.error.unsupported_device",
    "config.abort.already_configured",
    "config.abort.reconfigure_successful",
    "config.abort.wrong_device",
    "entity.climate.s21climate.state_attributes.fan_mode.state.custom",
    "entity.button.blauberg_s21_reset_filter.name",
    "entity.button.blauberg_s21_reset_alarm.name",
    "entity.switch.blauberg_s21_boost_switch.name",
    "entity.switch.blauberg_s21_timer_switch.name",
    "entity.switch.blauberg_s21_schedule_mode_switch.name",
    "entity.select.blauberg_s21_bypass_mode.name",
    "entity.select.blauberg_s21_bypass_mode.state.close",
    "entity.select.blauberg_s21_bypass_mode.state.open",
    "entity.select.blauberg_s21_bypass_mode.state.auto",
    "entity.number.s21manualfanspeed.name",
    "entity.sensor.s21_supply_outdoor_temperature.name",
    "entity.sensor.s21_supply_temperature.name",
    "entity.sensor.s21_extract_temperature.name",
    "entity.sensor.s21_extract_outlet_temperature.name",
    "services.reset_filter_change_timer.name",
    "services.reset_filter_change_timer.description",
    "services.reset_alarm.name",
    "services.reset_alarm.description",
)


def flatten(node: dict, prefix: str = "") -> dict[str, str]:
    flat: dict[str, str] = {}
    for key, value in node.items():
        full = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flat.update(flatten(value, full))
        else:
            flat[full] = value
    return flat


@pytest.fixture(scope="module")
def manifest(component_dir):
    return json.loads((component_dir / "manifest.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def strings(component_dir):
    return json.loads((component_dir / "strings.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def services(component_dir):
    return yaml.safe_load((component_dir / "services.yaml").read_text(encoding="utf-8"))


# --------------------------------------------------------------------- manifest
def test_manifest_has_the_keys_hacs_and_hassfest_need(manifest):
    for key in (
        "domain",
        "name",
        "codeowners",
        "config_flow",
        "documentation",
        "iot_class",
        "issue_tracker",
        "requirements",
        "version",
    ):
        assert key in manifest, f"manifest.json is missing {key}"

    assert manifest["domain"] == DOMAIN
    assert manifest["config_flow"] is True
    assert manifest["iot_class"] == "local_polling"


def test_manifest_declares_its_logger(manifest):
    assert manifest.get("loggers") == [f"custom_components.{DOMAIN}"]


def test_requirements_do_not_pin_pymodbus_too_tightly(manifest):
    """Home Assistant shares one pymodbus with its own modbus integration.

    Demanding a high minimum forces an upgrade of that shared dependency, so the
    floor stays low enough that any supported Home Assistant already satisfies it.
    """
    assert manifest["requirements"] == ["pymodbus>=3.6.0,<4.0"]


def test_the_vendored_client_is_present(component_dir):
    vendored = component_dir / "pybls21"
    assert (vendored / "__init__.py").is_file()
    for module in ("client", "constants", "exceptions", "models"):
        assert (vendored / f"{module}.py").is_file()


def test_hacs_manifest_is_valid(component_dir):
    hacs = json.loads((REPO_ROOT / "hacs.json").read_text(encoding="utf-8"))
    assert hacs["name"]


# --------------------------------------------------------------------- services
def test_services_are_targetable(services):
    """Without a target block the UI offers no entity picker."""
    assert services
    for name, body in services.items():
        assert "name" in body, f"{name} has no name"
        assert "description" in body, f"{name} has no description"
        target = body.get("target")
        assert target, f"{name} has no target selector"
        entity = target.get("entity", {})
        assert entity.get("integration") == DOMAIN
        assert entity.get("domain") == "climate"


def test_services_and_strings_agree(services, strings):
    assert set(services) == set(strings.get("services", {}))


def test_registered_services_match_services_yaml(services):
    """Everything declared in services.yaml must actually be registered."""
    source = (COMPONENT / "climate.py").read_text(encoding="utf-8")
    for name in services:
        assert f'"{name}"' in source, f"{name} is never registered in climate.py"


# ----------------------------------------------------------------- translations
def test_english_translations_exist(component_dir):
    assert (component_dir / "translations" / "en.json").is_file()


def test_translations_cover_strings_json(component_dir, strings):
    """strings.json is not served at runtime for custom integrations."""
    english = flatten(
        json.loads(
            (component_dir / "translations" / "en.json").read_text(encoding="utf-8")
        )
    )
    flat_strings = flatten(strings)
    missing = [
        key
        for key, value in flat_strings.items()
        # [%key:...%] entries are resolved from core's shared catalogue.
        if not str(value).startswith("[%key:") and key not in english
    ]
    assert not missing, f"translations/en.json is missing {missing}"


@pytest.mark.parametrize("path", LANGUAGE_FILES, ids=LANGUAGE_IDS)
def test_every_language_has_the_required_keys(path):
    flat = flatten(json.loads(path.read_text(encoding="utf-8")))
    missing = [key for key in REQUIRED_KEYS if key not in flat]
    assert not missing, f"{path.name} is missing {missing}"


@pytest.mark.parametrize("path", LANGUAGE_FILES, ids=LANGUAGE_IDS)
def test_no_translation_is_blank(path):
    flat = flatten(json.loads(path.read_text(encoding="utf-8")))
    blank = [key for key, value in flat.items() if not str(value).strip()]
    assert not blank, f"{path.name} has blank values for {blank}"


@pytest.mark.parametrize("path", LANGUAGE_FILES, ids=LANGUAGE_IDS)
def test_no_language_invents_extra_keys(path, component_dir):
    """Every localised key must also exist in English."""
    english = flatten(
        json.loads(
            (component_dir / "translations" / "en.json").read_text(encoding="utf-8")
        )
    )
    flat = flatten(json.loads(path.read_text(encoding="utf-8")))
    # Core supplies the shared low/medium/high fan mode names, so a language may
    # legitimately localise those without English needing to.
    allowed_extra = {
        "entity.climate.s21climate.state_attributes.fan_mode.state.low",
        "entity.climate.s21climate.state_attributes.fan_mode.state.medium",
        "entity.climate.s21climate.state_attributes.fan_mode.state.high",
    }
    extra = set(flat) - set(english) - allowed_extra
    assert not extra, f"{path.name} has keys English does not: {sorted(extra)}"


def test_bypass_select_options_match_the_translations(component_dir):
    """The select's options and its translated states must line up."""
    from blauberg_s21_ext.select import BYPASS_MODE_OPTIONS

    english = flatten(
        json.loads(
            (component_dir / "translations" / "en.json").read_text(encoding="utf-8")
        )
    )
    for option in BYPASS_MODE_OPTIONS:
        key = f"entity.select.blauberg_s21_bypass_mode.state.{option}"
        assert key in english, f"no translation for bypass option {option!r}"


def test_every_sensor_has_a_translated_name(component_dir):
    """Each sensor description's translation key must exist in every language."""
    from blauberg_s21_ext.sensor import TEMPERATURE_SENSORS

    for path in LANGUAGE_FILES:
        flat = flatten(json.loads(path.read_text(encoding="utf-8")))
        for description in TEMPERATURE_SENSORS:
            key = f"entity.sensor.{description.translation_key}.name"
            assert key in flat, f"{path.name} is missing {key}"


def test_sensor_keys_are_unique_and_stable(component_dir):
    """The keys double as unique id suffixes, so they must not change lightly."""
    from blauberg_s21_ext.sensor import TEMPERATURE_SENSORS

    keys = [description.key for description in TEMPERATURE_SENSORS]
    assert len(keys) == len(set(keys))
    assert set(keys) == {
        "supply_outdoor_temperature",
        "supply_temperature",
        "extract_temperature",
        "extract_outlet_temperature",
    }


def test_every_platform_module_is_registered(component_dir):
    """A platform file that is not in PLATFORMS would never be set up."""
    from blauberg_s21_ext import PLATFORMS

    registered = {str(platform) for platform in PLATFORMS}
    on_disk = {
        path.stem
        for path in component_dir.glob("*.py")
        if path.stem
        in {"climate", "switch", "button", "select", "sensor", "number", "binary_sensor"}
    }
    assert on_disk == registered, (
        f"platform modules and PLATFORMS disagree: "
        f"only on disk={on_disk - registered}, only registered={registered - on_disk}"
    )


def test_readme_points_at_the_right_logger(component_dir):
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert f"custom_components.{DOMAIN}" in readme
    assert "custom_components.blauberg_21_ext" not in readme, "typo in logger name"
