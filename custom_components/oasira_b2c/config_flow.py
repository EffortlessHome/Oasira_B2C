"""Config flow for the Oasira Home integration.

A Home Assistant hub is paired by redeeming a one-time enrollment code with the
Oasira Home Cloud. The code is exchanged for a revocable, per-hub
API key; no email/password (Firebase) login is required.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    DOMAIN,
    NAME,
)

_LOGGER = logging.getLogger(__name__)

CONF_API_URL = "api_url"
CONF_API_KEY = "api_key"
CONF_ENROLLMENT_CODE = "enrollment_code"
CONF_NODE_ID = "node_id"
CONF_NODE_NAME = "node_name"
CONF_HOME_ID = "home_id"
CONF_HOME_NAME = "home_name"

DEFAULT_API_URL = "https://homeapi.oasira.ai"
_URL_PATTERN = re.compile(r"^https?://[^\s/]+(?:/[^\s]*)?$", re.IGNORECASE)


class EnrollmentError(Exception):
    """Raised when a hub cannot be enrolled; ``reason`` is a translation key."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def is_hub_entry(entry: config_entries.ConfigEntry) -> bool:
    """Return True for entries paired with a hub API key."""
    return bool(entry.data.get(CONF_API_KEY))


def normalize_api_url(value: str) -> str | None:
    """Strip trailing slashes / version suffixes and validate the base URL."""
    url = (value or "").strip().rstrip("/")
    if url.endswith("/v1"):
        url = url[:-3]
    # The HA integration appends /api/ingest/enroll and /ingest/* itself.
    for suffix in ("/api/ingest", "/api/public", "/ingest", "/api"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
    return url if _URL_PATTERN.match(url) else None


async def async_enroll_hub(
    hass, api_url: str, code: str, node_name: str
) -> dict[str, Any]:
    """Exchange a one-time enrollment code for a revocable hub API key."""
    session = async_get_clientsession(hass)
    payload = {"code": code, "node_name": node_name}
    try:
        async with session.post(
            f"{api_url}/api/ingest/enroll",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as response:
            if response.status in (400, 401, 404):
                raise EnrollmentError("invalid_code")
            if response.status >= 400:
                raise EnrollmentError("cannot_connect")
            data = await response.json()
    except EnrollmentError:
        raise
    except (aiohttp.ClientError, TimeoutError) as err:
        _LOGGER.warning("Could not reach Oasira Home at %s: %s", api_url, err)
        raise EnrollmentError("cannot_connect") from err

    if not isinstance(data, dict) or not all(
        isinstance(data.get(key), str) and data[key]
        for key in ("api_key", "node_id", "home_id")
    ):
        raise EnrollmentError("cannot_connect")
    return data


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Pair a Home Assistant hub with an Oasira Home account."""

    VERSION = 3


    @staticmethod
    def async_supports_options_flow(config_entry: config_entries.ConfigEntry) -> bool:
        """Both pairings can configure a tunnel."""
        return False

    async def async_step_options_flow(self, user_input: dict | None = None) -> FlowResult:
        """Block options flow - integration is already configured."""
        # Always abort if an entry exists - prevent options reconfiguration
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        # Should not reach here, but return base case
        return self.async_abort(reason="unknown")

    async def async_step_user(self, user_input: dict | None = None) -> FlowResult:
        """Collect the Worker URL, one-time code and a name for this hub."""
        # Always abort if an entry already exists - remove reconfiguration option
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        errors: dict[str, str] = {}
        if user_input is not None:
            api_url = normalize_api_url(user_input[CONF_API_URL])
            code = (user_input.get(CONF_ENROLLMENT_CODE) or "").strip()
            node_name = (user_input.get(CONF_NODE_NAME) or "").strip() or self.hass.config.location_name
            if api_url is None:
                errors[CONF_API_URL] = "invalid_url"
            elif len(code.replace(" ", "").replace("-", "")) < 12:
                errors[CONF_ENROLLMENT_CODE] = "invalid_code"
            else:
                try:
                    result = await async_enroll_hub(self.hass, api_url, code, node_name)
                except EnrollmentError as err:
                    errors["base"] = err.reason
                else:
                    await self.async_set_unique_id(result["node_id"])
                    self._abort_if_unique_id_configured()
                    home_name = result.get("home_name") or result["home_id"]
                    return self.async_create_entry(
                        title=f"{NAME} - {home_name} - {result.get('node_name') or node_name}",
                        data={
                            CONF_API_URL: api_url,
                            CONF_API_KEY: result["api_key"],
                            CONF_NODE_ID: result["node_id"],
                            CONF_NODE_NAME: result.get("node_name") or node_name,
                            CONF_HOME_ID: result["home_id"],
                            CONF_HOME_NAME: home_name,
                        },
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_API_URL,
                        default=(user_input or {}).get(CONF_API_URL, DEFAULT_API_URL),
                    ): str,
                    vol.Required(CONF_ENROLLMENT_CODE): str,
                    vol.Optional(
                        CONF_NODE_NAME,
                        default=(user_input or {}).get(
                            CONF_NODE_NAME, self.hass.config.location_name
                        ),
                    ): str,
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        """The hub key was revoked; pair again with a new code."""
        # Block reauth if an entry already exists (prevents reconfiguration via reauth)
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict | None = None
    ) -> FlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            code = (user_input.get(CONF_ENROLLMENT_CODE) or "").strip()
            try:
                result = await async_enroll_hub(
                    self.hass,
                    entry.data[CONF_API_URL],
                    code,
                    entry.data.get(CONF_NODE_NAME, ""),
                )
            except EnrollmentError as err:
                errors["base"] = err.reason
            else:
                # A code for a different home must not take over this entry.
                if result["home_id"] != entry.data.get(CONF_HOME_ID):
                    return self.async_abort(reason="wrong_home")
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=result["node_id"],
                    data={
                        **entry.data,
                        CONF_API_KEY: result["api_key"],
                        CONF_NODE_ID: result["node_id"],
                        CONF_NODE_NAME: result.get("node_name") or entry.data.get(CONF_NODE_NAME, ""),
                    },
                    reason="reauth_successful",
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_ENROLLMENT_CODE): str}),
            errors=errors,
            description_placeholders={
                "home": entry.data.get(CONF_HOME_NAME, ""),
                "node": entry.data.get(CONF_NODE_NAME, ""),
            },
        )

