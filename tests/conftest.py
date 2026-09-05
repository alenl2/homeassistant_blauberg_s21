"""Shared pytest fixtures for the Blauberg S21 Extended test suite.

The suite has two halves:

* Offline tests, which run anywhere. They use :mod:`tests.fake_s21`, a fake
  Modbus server bound to the loopback interface on an ephemeral port, so nothing
  needs configuring.
* Live tests, which talk to real hardware. They are skipped unless the
  ``BLAUBERG_S21_HOST`` environment variable names a unit, so no address is ever
  committed to the repository.

    # PowerShell
    $env:BLAUBERG_S21_HOST = "<your-unit>"; pytest

    # bash
    BLAUBERG_S21_HOST=<your-unit> pytest
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPONENT_DIR = REPO_ROOT / "custom_components" / "blauberg_s21_ext"

# The integration is imported as `blauberg_s21_ext`, the way Home Assistant
# loads it from the custom_components directory.
sys.path.insert(0, str(REPO_ROOT / "custom_components"))
# ...and the vendored client is also importable on its own for the client tests.
sys.path.insert(0, str(COMPONENT_DIR))

ENV_HOST = "BLAUBERG_S21_HOST"
ENV_PORT = "BLAUBERG_S21_PORT"

#: Reserved for documentation by RFC 5737 (TEST-NET-1), so it is guaranteed not
#: to belong to anyone and never routes anywhere.
UNROUTABLE_HOST = "192.0.2.1"

# The Home Assistant stub has to be in place before any test module imports the
# integration. It steps aside automatically if a real Home Assistant is present.
from tests import hass_stub  # noqa: E402

HASS_STUB_ACTIVE = hass_stub.install()

requires_hass_stub = pytest.mark.skipif(
    not HASS_STUB_ACTIVE,
    reason=(
        "a real Home Assistant is installed; run these against "
        "pytest-homeassistant-custom-component instead of the stub"
    ),
)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "live: requires a real Blauberg S21 (set BLAUBERG_S21_HOST)"
    )
    config.addinivalue_line("markers", "slow: takes more than a few seconds")

    # pymodbus logs every frame byte-by-byte at DEBUG, which buries test output.
    logging.getLogger("pymodbus").setLevel(logging.CRITICAL)


@pytest.fixture(scope="session")
def component_dir() -> Path:
    """Path to the integration package."""
    return COMPONENT_DIR


@pytest.fixture(scope="session")
def live_target() -> tuple[str, int]:
    """Address of a real unit, or skip the test."""
    host = os.environ.get(ENV_HOST)
    if not host:
        pytest.skip(f"set {ENV_HOST} to run tests against real hardware")
    return host, int(os.environ.get(ENV_PORT, "502"))


@pytest.fixture
def unroutable_target() -> tuple[str, int]:
    """An address that never answers, for timeout behaviour."""
    return UNROUTABLE_HOST, 502


@pytest.fixture
def closed_port() -> tuple[str, int]:
    """A loopback port with nothing listening, for instant connection refusal."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return "127.0.0.1", port


@pytest.fixture
async def fake_server():
    """A fake single-connection S21 on the loopback interface."""
    from tests.fake_s21 import FakeS21Server

    server = FakeS21Server()
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


@pytest.fixture
async def client(fake_server):
    """An S21Client wired to the fake server, always closed afterwards."""
    from pybls21.client import S21Client

    host, port = fake_server.address
    instance = S21Client(host, port)
    try:
        yield instance
    finally:
        await instance.close()


@pytest.fixture
def established_sockets():
    """Count this process's established sockets to a given address.

    Used to prove the client never leaks a connection. Reads from /proc or
    netstat would be brittle across platforms, so this inspects the socket list
    the OS reports for our own process via psutil when available, and otherwise
    falls back to a best-effort check that is skipped.
    """

    def _count(host: str, port: int) -> int:
        try:
            import psutil
        except ImportError:
            pytest.skip("psutil is required for socket leak assertions")
        current = psutil.Process()
        count = 0
        for conn in current.net_connections(kind="tcp"):
            if (
                conn.raddr
                and conn.raddr.ip == host
                and conn.raddr.port == port
                and conn.status == psutil.CONN_ESTABLISHED
            ):
                count += 1
        return count

    return _count


@pytest.fixture
def pending_tasks():
    """Return the asyncio tasks still running, excluding the current one."""

    def _pending() -> list[asyncio.Task]:
        current = asyncio.current_task()
        return [
            task
            for task in asyncio.all_tasks()
            if task is not current and not task.done()
        ]

    return _pending


@pytest.fixture
def hass_stub_hass():
    """A fresh stubbed HomeAssistant instance."""
    if not HASS_STUB_ACTIVE:
        pytest.skip("Home Assistant stub is not active")
    from homeassistant.core import HomeAssistant

    return HomeAssistant()


@pytest.fixture
def config_entry_factory():
    """Build config entries pointing at an arbitrary host/port."""
    if not HASS_STUB_ACTIVE:
        pytest.skip("Home Assistant stub is not active")
    from blauberg_s21_ext.const import DOMAIN
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.const import CONF_HOST, CONF_PORT

    counter = {"n": 0}

    def _make(host: str, port: int, *, unique_id: str | None = None) -> ConfigEntry:
        counter["n"] += 1
        return ConfigEntry(
            domain=DOMAIN,
            data={CONF_HOST: host, CONF_PORT: port},
            title="Blauberg S21",
            unique_id=unique_id if unique_id is not None else f"S21_{host}_{port}",
            entry_id=f"entry_{counter['n']}",
        )

    return _make


@pytest.fixture
def add_entities(translations):
    """Run a platform's async_setup_entry and return the entities it created.

    Entities are given the integration's real translation catalogue so that name
    resolution is exercised end to end, which catches missing translation keys.
    """
    from blauberg_s21_ext.const import DOMAIN

    async def _setup(platform, hass, entry) -> list:
        created: list = []

        def _callback(entities, update_before_add: bool = False) -> None:
            created.extend(entities)

        await platform.async_setup_entry(hass, entry, _callback)

        platform_domain = platform.__name__.rsplit(".", 1)[-1]
        for index, entity in enumerate(created):
            entity.hass = hass
            entity.entity_id = f"{platform_domain}.blauberg_s21_{index}"
            entity.platform_domain = platform_domain
            entity.integration_domain = DOMAIN
            entity.platform_translations = translations
            await entity.async_added_to_hass()
        return created

    return _setup


@pytest.fixture
def translations(component_dir):
    """The integration's real English translations, flattened HA-style."""
    import json

    def _flatten(node: dict, prefix: str) -> dict[str, str]:
        flat: dict[str, str] = {}
        for key, value in node.items():
            full = f"{prefix}.{key}"
            if isinstance(value, dict):
                flat.update(_flatten(value, full))
            else:
                flat[full] = value
        return flat

    from blauberg_s21_ext.const import DOMAIN

    raw = json.loads(
        (component_dir / "translations" / "en.json").read_text(encoding="utf-8")
    )
    return _flatten(raw, f"component.{DOMAIN}")
