"""Device tracker platform for Oasira Home integration."""

from __future__ import annotations

from typing import Any

import logging

import aiohttp

from homeassistant.components.device_tracker import TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, NAME
_LOGGER = logging.getLogger(__name__)

# Domain data keys (matching those stored in __init__.py)
DOMAIN_API_URL = "api_url"
DOMAIN_API_KEY = "api_key"


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up device trackers for Oasira Home users."""
    domain_data = hass.data.get(DOMAIN, {})

    # Get API credentials from domain data (set during config entry setup)
    api_url = domain_data.get(DOMAIN_API_URL)
    api_key = domain_data.get(DOMAIN_API_KEY)

    if not all([api_url, api_key]):
        _LOGGER.debug(
            "Oasira device tracker: Missing required config data, skipping setup"
        )
        return

    # Fetch user accounts from the Oasira Home API
    users = await _async_fetch_users(hass, api_url, api_key)

    if not users:
        _LOGGER.debug("Oasira device tracker: No user accounts found")
        return

    # Create a device tracker entity for each user
    entities = []
    for user in users:
        user_id = user.get("id") or user.get("user_id") or user.get("email", "").split("@")[0] if user.get("email") else "unknown"
        # Use the format device_tracker.oasira_<userid>
        entities.append(
            OasiraDeviceTracker(
                api_url=api_url,
                api_key=api_key,
                user_id=user_id,
                user_data=user,
            )
        )

    async_add_entities(entities, True)


async def _async_fetch_users(hass: HomeAssistant, api_url: str, api_key: str) -> list[dict[str, Any]]:
    """Fetch user accounts from the Oasira Home API."""
    try:
        session = async_get_clientsession(hass)
        endpoint = f"{api_url.rstrip('/')}/api/ingest/me"

        async with session.get(
            endpoint,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as response:
            if response.status == 200:
                data = await response.json()
                # Extract user accounts from the response
                # The actual field names depend on the Oasira Home API response format
                users = data.get("users", [])
                if not users:
                    # Try alternative field names
                    users = data.get("account", [])
                if not users:
                    # Try single user format
                    users = [data] if data else []
                _LOGGER.debug(
                    "Oasira device tracker: Fetched %s user(s) from API", len(users)
                )
                return users
            elif response.status == 401:
                _LOGGER.warning(
                    "Oasira device tracker: Unauthorized access to API for user fetch"
                )
            else:
                _LOGGER.warning(
                    "Oasira device tracker: API returned %s for user fetch", response.status
                )
    except Exception as err:
        _LOGGER.warning(
            "Oasira device tracker: Error fetching users: %s", err
        )
    return []


class OasiraDeviceTracker(TrackerEntity, RestoreEntity):
    """Representation of an Oasira user device tracker."""

    _attr_has_entity_name = True
    _attr_name = None  # Entity name handled by TrackerEntity

    def __init__(
        self,
        api_url: str,
        api_key: str,
        user_id: str,
        user_data: dict[str, Any],
    ) -> None:
        """Initialize the Oasira device tracker."""
        self._api_url = api_url
        self._api_key = api_key
        self._user_id = user_id
        self._user_data = user_data or {}

        # Set unique ID based on user_id: device_tracker.oasira_<userid>
        self._attr_unique_id = f"oasira_{user_id}_device_tracker"

        # Set entity ID suffix for proper naming
        self._attr_should_poll = False

        # Set device info
        self._attr_device_info = {
            "identifiers": {(DOMAIN, NAME)},
            "name": f"{NAME} - User {user_id}",
            "manufacturer": NAME,
            "model": NAME,
            "sw_version": "1.0",
        }

        # Initialize location state
        self._attr_location_name = None
        self._attr_gps = None
        self._attr_source = "gps"  # default source

    @property
    def device_info(self):
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, NAME)},
            "name": f"{NAME} - User {self._user_id}",
            "manufacturer": NAME,
            "model": NAME,
        }

    async def async_update(self) -> None:
        """Update the device tracker state from the Oasira Home API."""
        try:
            session = async_get_clientsession(self.hass)
            endpoint = f"{self._api_url.rstrip('/')}/api/ingest/me"

            async with session.get(
                endpoint,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    # Extract location/presence data from the response
                    state = self._parse_api_response(data)
                    self._attr_location_name = state.get("location")
                    self._attr_gps = state.get("gps")
                    self._attr_source = state.get("source", "gps")
                elif response.status == 401:
                    _LOGGER.warning(
                        "Oasira device tracker: Unauthorized access to API"
                    )
                else:
                    _LOGGER.warning(
                        "Oasira device tracker: API returned %s", response.status
                    )
        except Exception as err:
            _LOGGER.warning(
                "Oasira device tracker: Error updating state: %s", err
            )

    def _async_get_session(self) -> aiohttp.ClientSession:
        """Get or create an aiohttp session."""
        return async_get_clientsession(self.hass)

    def _parse_api_response(self, data: dict[str, Any]) -> dict[str, Any]:
        """Parse the Oasira Home API response for tracker data."""
        # Default fallback values
        result: dict[str, Any] = {
            "location": None,
            "gps": None,
            "source": "gps",
        }

        # Try to extract location information from various possible fields
        # The actual field names depend on the Oasira Home API response format
        if data:
            # Common fields that might contain location info
            for key in ["location", "gps", "position", "coordinates", "home_position", "status"]:
                if key in data and data[key]:
                    result[key] = data[key]

            # If no direct location, try to derive from user status
            if not result.get("location"):
                # Check for presence-related data
                presence = data.get("presence") or data.get("status") or data.get("home_status")
                if presence:
                    presence_str = str(presence).lower()
                    if presence_str in ("home", "present", "inside"):
                        result["location"] = "home"
                    elif presence_str in ("away", "outside", "not_home"):
                        result["location"] = "away"

        return result