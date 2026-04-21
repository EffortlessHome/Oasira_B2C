from __future__ import annotations

import json
import logging
from typing import Optional, List, Dict, Any
import aiohttp
import time
from google.auth import jwt
from google.auth.crypt import rsa

from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import async_get as async_get_dev_reg
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.restore_state import RestoreEntity

from oasira import OasiraAPIClient, OasiraAPIError
from .const import DOMAIN, NAME
from .oasiranotificationdevice import oasiranotificationdevice

_LOGGER = logging.getLogger(__name__)


GOOGLE_OAUTH_URL = "https://oauth2.googleapis.com/token"
FIREBASE_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"

FCM_URL = "https://fcm.googleapis.com/v1/projects/oasira-oauth/messages:send"


class OasiraPerson(SensorEntity, RestoreEntity):
    """A persistent, sensor-like representation of an Oasira Person with tracking and notifications."""

    def __init__(self, hass: Optional[HomeAssistant], email: str):
        self.hass = hass
        self._email = email
        self._attr_name = email
        self._attr_unique_id = f"oasira_person_{email.lower().replace('@', '_').replace('.', '_')}"
        self._attr_icon = "mdi:account"
        self._attr_should_poll = False

        self._local_tracker_entity_id: Optional[str] = None
        self._remote_tracker_entity_id: Optional[str] = None
        self._notification_devices: List[oasiranotificationdevice] = []
        self._health_data: Dict[str, Any] = {}

        # Device registry
        self._device_registry = async_get_dev_reg(hass) if hass else None
        self._device_id = None

        

    # ---- Standard HA Properties ----
    @property
    def unique_id(self) -> str:
        return self._attr_unique_id

    @property
    def icon(self) -> str:
        return "mdi:account-group"

    @property
    def state(self) -> str:
        return self.remotetracker + "|"+ self.localtracker

    @property
    def name(self) -> str:
        return self._email

    @property
    def notification_devices(self) -> List[oasiranotificationdevice]:
        return self._notification_devices

    @property
    def device_info(self) -> Dict[str, Any]:
        return {
            "identifiers": {(DOMAIN, NAME)},
            "name": NAME,
            "manufacturer": NAME,
        }

    @property
    def localtracker(self) -> str:
        # Local tracker
        if self._local_tracker_entity_id:
            entity = self.hass.states.get(self._local_tracker_entity_id)
            if entity is not None:
                return entity.state
            else:
                return "unknown"
        else:
            return "unknown"


    @property
    def remotetracker(self) -> str:
        # Remote tracker
        if self._remote_tracker_entity_id:
            entity = self.hass.states.get(self._remote_tracker_entity_id)
            if entity is not None:
                return entity.state
            else:
                return "unknown"
        else:
            return "unknown"

    async def async_added_to_hass(self) -> None:
        """Restore previous state on startup."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and last_state.attributes:
            attrs = last_state.attributes
            self._local_tracker_entity_id = attrs.get("local_tracker")
            self._remote_tracker_entity_id = attrs.get("remote_tracker")
            if "notification_devices" in attrs:
                try:
                    devices_data = attrs["notification_devices"]
                    if isinstance(devices_data, list):
                        import json
                        self._notification_devices = [
                            oasiranotificationdevice.from_dict(
                                json.loads(d) if isinstance(d, str) else d
                            )
                            for d in devices_data
                        ]
                except Exception as err:
                    _LOGGER.debug("Failed to restore notification devices: %s", err)

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return attributes for Home Assistant."""

        return {
            "oasira_type": "OasiraPerson",
            "email": self._email,
            "local_tracker": self._local_tracker_entity_id,
            "remote_tracker": self._remote_tracker_entity_id,
            "notification_devices": [d.to_json() for d in self._notification_devices],
            "health_data_last_updated": self._health_data.get("timestamp"),
        }

    # ---- Serialization ----
    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "email": self._email,
            "unique_id": self._attr_unique_id,
            "local_tracker": self._local_tracker_entity_id,
            "remote_tracker": self._remote_tracker_entity_id,
            "notification_devices": [d.to_dict() for d in self._notification_devices],
        }

    def to_json(self) -> str:
        """Return JSON string representation."""
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OasiraPerson":
        """Reconstruct from serialized dictionary (hass can be attached later)."""
        obj = cls(
            hass=None,
            email=data.get("email", ""),
        )
        obj._local_tracker_entity_id = data.get("local_tracker")
        obj._remote_tracker_entity_id = data.get("remote_tracker")
        obj._notification_devices = [
            oasiranotificationdevice.from_dict(d)
            for d in data.get("notification_devices", [])
        ]
        return obj

    # ---- Device linking ----
    async def async_set_local_tracker(self, entity_id: str):
        self._local_tracker_entity_id = entity_id
        _LOGGER.info("[OasiraPerson] Linked local tracker for %s: %s", self._email, entity_id)
        self.async_write_ha_state()

    async def async_set_remote_tracker(self, entity_id: str):
        self._remote_tracker_entity_id = entity_id
        _LOGGER.info("[OasiraPerson] Linked remote tracker for %s: %s", self._email, entity_id)
        self.async_write_ha_state()

    async def async_set_notification_devices(
        self, hass: HomeAssistant, token: str, device_name: str, platform_name: str
    ):
        """Link a notification device."""
        if not token:
            _LOGGER.warning("[OasiraPerson] Missing token for notification registration.")
            return

        existing = next((d for d in self._notification_devices if d.Name == device_name), None)
        if existing:
            _LOGGER.info("[OasiraPerson] Device %s already registered for %s", device_name, self._email)

            #update mode
            existing.DeviceToken = token
            existing.Platform = platform_name
            self.async_write_ha_state()
            _LOGGER.info("[OasiraPerson] Updated notification device %s for %s", device_name, self._email)

        else:
            device = oasiranotificationdevice(hass, token, device_name, platform_name)
            self._notification_devices.append(device)
            self.async_write_ha_state()
            _LOGGER.info("[OasiraPerson] Added notification device %s for %s", device_name, self._email)

    async def async_remove_notification_devices(
        self, hass: HomeAssistant
    ):
        """Remove all notification devices."""
        if not self._notification_devices:
            _LOGGER.info("[OasiraPerson] No notification devices to remove for %s", self._email)
            return      
        else:
            self._notification_devices.clear()
            self.async_write_ha_state()
            _LOGGER.info("[OasiraPerson] Removed all notification devices for %s", self._email)


    async def async_added_to_hass(self):
        """Handle entity addition and restore previous state."""
        await super().async_added_to_hass()

        if (last_state := await self.async_get_last_state()) is None:
            _LOGGER.info("[OasiraPerson] No previous state to restore for %s", self._email)
            return

        attrs = last_state.attributes or {}
        self._local_tracker_entity_id = attrs.get("local_tracker")
        self._remote_tracker_entity_id = attrs.get("remote_tracker")

        restored_devices_raw = attrs.get("notification_devices")
        _LOGGER.debug("[OasiraPerson] Raw restored notification_devices: (%s) %r",
                    type(restored_devices_raw).__name__, restored_devices_raw)

        devices_list: list = []

        def try_parse_json_string(s: str) -> list:
            """Try several ways to parse a JSON string into a list of dicts."""
            s_stripped = s.strip()
            _LOGGER.debug("[OasiraPerson] Attempting to parse JSON string chunk: %r", s_stripped[:200])
            # 1) Try direct load (handles object or array)
            try:
                parsed = json.loads(s_stripped)
                if isinstance(parsed, list):
                    _LOGGER.debug("[OasiraPerson] Parsed JSON as list with %d items", len(parsed))
                    return parsed
                if isinstance(parsed, dict):
                    _LOGGER.debug("[OasiraPerson] Parsed JSON as single object")
                    return [parsed]
            except json.JSONDecodeError as e:
                _LOGGER.debug("[OasiraPerson] Direct json.loads failed: %s", e)

            # 2) If looks like multiple objects without surrounding brackets, try wrapping
            if s_stripped.startswith("{") and s_stripped.endswith("}"):
                wrapped = f"[{s_stripped}]"
                try:
                    parsed = json.loads(wrapped)
                    _LOGGER.debug("[OasiraPerson] Parsed by wrapping in brackets -> %d items", len(parsed))
                    return parsed
                except json.JSONDecodeError as e:
                    _LOGGER.debug("[OasiraPerson] Wrapped json.loads failed: %s", e)

            # 3) As a last resort attempt to convert a "}, {" pattern into a valid array
            if "},{" in s_stripped or "}, {" in s_stripped:
                # Insert array brackets if missing
                candidate = s_stripped
                if not candidate.startswith("["):
                    candidate = "[" + candidate
                if not candidate.endswith("]"):
                    candidate = candidate + "]"
                try:
                    parsed = json.loads(candidate)
                    _LOGGER.debug("[OasiraPerson] Parsed by forcing array brackets -> %d items", len(parsed))
                    return parsed
                except json.JSONDecodeError as e:
                    _LOGGER.debug("[OasiraPerson] Forced-array json.loads failed: %s", e)

            _LOGGER.warning("[OasiraPerson] Failed to parse JSON chunk; skipping. chunk preview: %r", s_stripped[:200])
            return []

        # If it's a list, individual elements may be dicts or JSON strings
        if isinstance(restored_devices_raw, list):
            _LOGGER.debug("[OasiraPerson] notification_devices is a list with %d elements", len(restored_devices_raw))
            for idx, item in enumerate(restored_devices_raw):
                _LOGGER.debug("[OasiraPerson] Inspecting list element %d type=%s", idx, type(item).__name__)
                if isinstance(item, dict):
                    devices_list.append(item)
                elif isinstance(item, str):
                    parsed = try_parse_json_string(item)
                    devices_list.extend([p for p in parsed if isinstance(p, dict)])
                else:
                    _LOGGER.warning("[OasiraPerson] Unsupported list element type in notification_devices: %s", type(item))
        elif isinstance(restored_devices_raw, str):
            # The attribute is a string; it may contain one or many JSON objects (or an array string)
            parsed = try_parse_json_string(restored_devices_raw)
            devices_list.extend([p for p in parsed if isinstance(p, dict)])
        elif restored_devices_raw is None:
            _LOGGER.info("[OasiraPerson] No notification_devices attribute to restore for %s", self._email)
        else:
            _LOGGER.warning("[OasiraPerson] Unexpected type for notification_devices: %s", type(restored_devices_raw))

        _LOGGER.debug("[OasiraPerson] Devices parsed count=%d : %s",
                    len(devices_list), [d.get("name") for d in devices_list])

        # Reconstruct device objects safely
        restored_objs = []
        for d_idx, d in enumerate(devices_list):
            if not isinstance(d, dict):
                _LOGGER.debug("[OasiraPerson] Skipping non-dict device entry at index %d: %r", d_idx, d)
                continue
            try:
                # prefer a from_dict constructor if available
                if hasattr(oasiranotificationdevice, "from_dict"):
                    obj = oasiranotificationdevice.from_dict(d)
                    # attach hass if possible so the object behaves inside HA
                    try:
                        obj.hass = self.hass
                    except Exception:
                        pass
                else:
                    # fallback to direct init using expected fields
                    obj = oasiranotificationdevice(
                        self.hass,
                        token=d.get("token", ""),
                        name=d.get("name", d.get("unique_id", "unknown")),
                        platform=d.get("platform", ""),
                    )
                    obj._state = d.get("state", "available")
                restored_objs.append(obj)
                _LOGGER.info("[OasiraPerson] Restored notification device: %s", getattr(obj, "Name", d.get("name")))
            except Exception as e:
                _LOGGER.exception("[OasiraPerson] Failed to reconstruct device from dict %r: %s", d, e)

        self._notification_devices = restored_objs

        _LOGGER.info(
            "[OasiraPerson] Restored %d notification devices for %s",
            len(self._notification_devices),
            self._email,
        )

    async def async_get_firebase_access_token(self) -> str:
        """Generate a Firebase access token using service account JSON (async + HA safe)."""

        try:
            # ---- Fetch service account JSON using API client ----
            # Get the id_token from hass.data for authentication
            id_token = self.hass.data[DOMAIN].get("id_token") if self.hass else None
            
            async with OasiraAPIClient(id_token=id_token) as client:
                firebase_config = await client.get_firebase_config()

            google_firebase_raw = firebase_config.get("Google_Firebase")
            if not google_firebase_raw:
                _LOGGER.error("Missing Google_Firebase in response")
                return None

            service_account_info = json.loads(google_firebase_raw)

            private_key = service_account_info["private_key"]
            client_email = service_account_info["client_email"]

            # ---- Build JWT ----
            now = int(time.time())
            payload = {
                "iss": client_email,
                "scope": FIREBASE_SCOPE,
                "aud": GOOGLE_OAUTH_URL,
                "iat": now,
                "exp": now + 3600,
            }

            signer = rsa.RSASigner.from_string(private_key)
            assertion = jwt.encode(signer, payload)

            # ---- Exchange JWT for OAuth access token ----
            form = {
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(GOOGLE_OAUTH_URL, data=form) as resp:
                    result = await resp.json()

                    if "access_token" not in result:
                        _LOGGER.error("Firebase OAuth error: %s", result)
                        return None

                    return result["access_token"]

        except OasiraAPIError as e:
            _LOGGER.error("Failed to fetch Firebase config: %s", e)
            return None
        except Exception as e:
            _LOGGER.exception("Failed to refresh Firebase access token: %s", e)
            return None



    async def async_send_notification(self, message: str, title: str = None, data: dict = None):
        """Send push notifications to all registered devices."""

        if not self._notification_devices:
            _LOGGER.warning("[OasiraPerson] No registered devices for %s", self._email)
            return

        access_token = await self.async_get_firebase_access_token()
        if not access_token:
            _LOGGER.error("Failed to obtain fresh Firebase token — notification aborted.")
            return

        for device in self._notification_devices:
            if not device.DeviceToken:
                continue

            #TODO: Jermie: re-enable data payload
            #payload = {
            #    "message": {
            #        "token": device.DeviceToken,
            #        "notification": {
            #            "title": title or "Notification",
            #            "body": message,
            #        },
            #        "data": data or {},
            #    }
            #}

            payload = {
                "message": {
                    "token": device.DeviceToken,
                    "notification": {
                        "title": title or "Notification",
                        "body": message,
                    },
                }
            }

            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; UTF-8",
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(FCM_URL, headers=headers, json=payload) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        _LOGGER.error("FCM error %s: %s", resp.status, body)
                    else:
                        _LOGGER.info("Notification sent to %s", device.Name)

    async def async_update_health_data(self, health_data: Dict[str, Any]):
        """Update health data for this person."""
        _LOGGER.info("[OasiraPerson] Updating health data for %s", self._email)
        
        # Store the health data
        self._health_data = health_data
        
        # Trigger state update to reflect new health data timestamp in attributes
        if self.hass:
            self.async_write_ha_state()

    def __repr__(self):
        return f"<OasiraPerson email={self._email!r} devices={len(self._notification_devices)}>"
