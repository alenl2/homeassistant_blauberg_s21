"""Config flow for Blauberg S21 integration."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN
from .pybls21.client import S21Client
from .pybls21.exceptions import UnsupportedDeviceException

STEP_USER_DATA_SCHEMA = vol.Schema(
    {vol.Required(CONF_HOST): str, vol.Required(CONF_PORT, default=502): int}
)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect.

    Data has the keys from STEP_USER_DATA_SCHEMA with values provided by the user.
    """
    host = data[CONF_HOST]
    port = data[CONF_PORT]

    client = S21Client(host, port)
    try:
        await client.poll()
    except UnsupportedDeviceException:
        raise
    except Exception as exception:
        raise CannotConnect from exception
    finally:
        # Always hand the socket back; the unit only accepts one connection and
        # reserves it for ~57 s if the peer disappears without closing.
        await client.close()

    device = client.device
    title = (
        device.name
        if device and getattr(device, "name", None)
        else f"Blauberg S21 ({host}:{port})"
    )
    unique_id = (
        str(device.unique_id)
        if device and getattr(device, "unique_id", None)
        else f"{str(host).lower()}:{port}"
    )

    return {"title": title, "unique_id": unique_id}


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Blauberg S21."""

    VERSION = 1
    MINOR_VERSION = 1

    async def _async_validate(
        self, user_input: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, dict[str, str]]:
        """Validate user input, returning (info, errors)."""
        errors: dict[str, str] = {}
        try:
            info = await validate_input(self.hass, user_input)
        except CannotConnect:
            errors["base"] = "cannot_connect"
        except UnsupportedDeviceException:
            errors["base"] = "unsupported_device"
        except Exception:  # noqa: BLE001 - surfaced to the user as "unknown"
            errors["base"] = "unknown"
        else:
            return info, errors
        return None, errors

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        if user_input is None:
            return self.async_show_form(
                step_id="user", data_schema=STEP_USER_DATA_SCHEMA
            )

        info, errors = await self._async_validate(user_input)
        if info is not None:
            await self.async_set_unique_id(info["unique_id"])
            self._abort_if_unique_id_configured()
            self._async_abort_entries_match(
                {CONF_HOST: user_input[CONF_HOST], CONF_PORT: user_input[CONF_PORT]}
            )
            return self.async_create_entry(title=info["title"], data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Let the user point an existing entry at a new host/port.

        The unique id is derived from the host, so without this a DHCP change
        would force the user to delete and re-add the device, losing history.
        """
        entry = self._get_reconfigure_entry()

        if user_input is None:
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=self.add_suggested_values_to_schema(
                    STEP_USER_DATA_SCHEMA, entry.data
                ),
            )

        info, errors = await self._async_validate(user_input)
        if info is not None:
            await self.async_set_unique_id(info["unique_id"])
            self._abort_if_unique_id_mismatch(reason="wrong_device")
            return self.async_update_reload_and_abort(entry, data=user_input)

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_DATA_SCHEMA, user_input
            ),
            errors=errors,
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""
