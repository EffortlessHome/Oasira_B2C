"""Person notification device management and broadcasting."""

from __future__ import annotations

import json
import logging
from typing import Any

from google.auth import jwt
from google.auth.crypt import rsa
from homeassistant.core import HomeAssistant
from homeassistant.helpers import storage

from .const import DOMAIN
from .oasiranotificationdevice import oasiranotificationdevice

_LOGGER = logging.getLogger(__name__)

PERSON_NOTIFICATION_DEVICES_KEY = "oasira_person_notification_devices"
PERSON_NOTIFICATION_DEVICES_VERSION = 1


class PersonNotificationManager:
    """Manages notification devices for individual persons."""

    def __init__(self, hass: HomeAssistant):
        """Initialize the manager."""
        self.hass = hass
        self.store = storage.Store(
            hass,
            PERSON_NOTIFICATION_DEVICES_VERSION,
            PERSON_NOTIFICATION_DEVICES_KEY,
        )
        self._devices_by_person: dict[str, list[oasiranotificationdevice]] = {}

    async def async_load(self) -> None:
        """Load person-device mappings from storage."""
        try:
            data = await self.store.async_load() or {}
            self._devices_by_person = {}

            for email, device_list in data.items():
                if isinstance(device_list, list):
                    self._devices_by_person[email] = [
                        oasiranotificationdevice.from_dict(
                            dev if isinstance(dev, dict) else json.loads(dev)
                        )
                        for dev in device_list
                        if isinstance(dev, (dict, str))
                    ]

            _LOGGER.debug(
                "Loaded person-device mappings for %d people", len(self._devices_by_person)
            )
        except Exception as e:
            _LOGGER.error("Failed to load person notification devices: %s", e)
            self._devices_by_person = {}

    async def async_save(self) -> None:
        """Persist person-device mappings to storage."""
        try:
            data = {
                email: [dev.to_dict() for dev in devices]
                for email, devices in self._devices_by_person.items()
            }
            await self.store.async_save(data)
        except Exception as e:
            _LOGGER.error("Failed to save person notification devices: %s", e)

    def get_devices_for_person(self, email: str) -> list[oasiranotificationdevice]:
        """Get all notification devices for a person."""
        return list(self._devices_by_person.get(email, []))

    async def add_device_to_person(
        self,
        email: str,
        token: str,
        device_name: str,
        platform: str,
    ) -> bool:
        """Add a notification device to a person."""
        if not email or not token or not device_name or not platform:
            _LOGGER.error(
                "Missing required fields: email=%s, token=%s, device_name=%s, platform=%s",
                bool(email),
                bool(token),
                bool(device_name),
                bool(platform),
            )
            return False

        if email not in self._devices_by_person:
            self._devices_by_person[email] = []

        devices = self._devices_by_person[email]
        existing_idx = None
        for idx, dev in enumerate(devices):
            if dev.DeviceToken == token:
                existing_idx = idx
                break

        device = oasiranotificationdevice(
            hass=self.hass,
            token=token,
            name=device_name,
            platform=platform,
        )

        if existing_idx is not None:
            devices[existing_idx] = device
            _LOGGER.info("Updated device '%s' for %s", device_name, email)
        else:
            devices.append(device)
            _LOGGER.info("Added device '%s' to %s", device_name, email)

        await self.async_save()
        return True

    async def remove_device_from_person(self, email: str, device_name: str) -> bool:
        """Remove a notification device from a person."""
        if email not in self._devices_by_person:
            _LOGGER.warning("Person %s not found in notification mappings", email)
            return False

        devices = self._devices_by_person[email]
        before_count = len(devices)
        devices[:] = [dev for dev in devices if dev.Name != device_name]

        if len(devices) < before_count:
            if not devices:
                del self._devices_by_person[email]
            await self.async_save()
            _LOGGER.info("Removed device '%s' from %s", device_name, email)
            return True

        _LOGGER.warning(
            "Device '%s' not found for person %s", device_name, email
        )
        return False

    async def remove_all_devices_for_person(self, email: str) -> bool:
        """Remove all notification devices for a person."""
        if email in self._devices_by_person:
            del self._devices_by_person[email]
            await self.async_save()
            _LOGGER.info("Removed all devices for %s", email)
            return True

        return False


async def send_notification_to_person(
    hass: HomeAssistant,
    person_email: str,
    title: str,
    message: str,
    data: dict[str, Any] | None = None,
) -> bool:
    """Send a notification to a person on all their registered devices.

    Uses Firebase Cloud Messaging (FCM) to broadcast to all devices.
    """
    manager: PersonNotificationManager | None = hass.data.get(DOMAIN, {}).get(
        "person_notification_manager"
    )
    if not manager:
        _LOGGER.error("PersonNotificationManager not initialized")
        return False

    devices = manager.get_devices_for_person(person_email)
    if not devices:
        _LOGGER.warning("No notification devices registered for %s", person_email)
        return False

    tokens = [dev.DeviceToken for dev in devices]
    _LOGGER.debug("Sending notification to %s on %d devices", person_email, len(tokens))

    domain_data = hass.data.get(DOMAIN, {})

    try:
        access_token, project_id = await _get_firebase_access_token(hass, domain_data)
        if not access_token or not project_id:
            _LOGGER.error("Unable to get Firebase access token for person notification")
            return False

        fcm_url = f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"

        from homeassistant.helpers.aiohttp_client import async_get_clientsession

        session = async_get_clientsession(hass)
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        failed_count = 0
        for token in tokens:
            payload = {
                "message": {
                    "token": token,
                    "notification": {
                        "title": title,
                        "body": message,
                    },
                }
            }

            if data:
                payload["message"]["data"] = {
                    str(k): str(v) for k, v in data.items()
                }

            try:
                async with session.post(
                    fcm_url, headers=headers, json=payload, timeout=10
                ) as resp:
                    if resp.status not in (200, 201):
                        text = await resp.text()
                        _LOGGER.error("FCM error for %s: %s", person_email, text)
                        failed_count += 1
                    else:
                        _LOGGER.debug("Notification sent to device for %s", person_email)

            except Exception as e:
                _LOGGER.error("Failed to send FCM to device: %s", e)
                failed_count += 1

        if failed_count > 0:
            _LOGGER.warning(
                "Sent notification to %s with %d/%d device failures",
                person_email,
                failed_count,
                len(tokens),
            )
            return False

        _LOGGER.info(
            "Successfully sent notification to %s on %d devices",
            person_email,
            len(tokens),
        )
        return True

    except Exception as e:
        _LOGGER.error("Failed to send notification to person %s: %s", person_email, e)
        return False


async def _get_firebase_access_token(
    hass: HomeAssistant, domain_data: dict[str, Any]
) -> tuple[str | None, str | None]:
    """Get a valid Firebase access token for sending FCM messages."""
    try:
        from oasira import OasiraAPIClient, OasiraAPIError
        import time
        id_token = hass.data.get(DOMAIN, {}).get("id_token")
        if not id_token:
            _LOGGER.error("Missing id_token for Firebase access")
            return None, None

        async with OasiraAPIClient(id_token=id_token) as client:
            firebase_config = await client.get_firebase_config()

        google_firebase_raw = (
            firebase_config.get("Google_Firebase") if firebase_config else None
        )
        if not google_firebase_raw:
            _LOGGER.error("Missing Google_Firebase config from Oasira")
            return None, None

        service_account_info = json.loads(google_firebase_raw)
        private_key = service_account_info["private_key"]
        client_email = service_account_info["client_email"]
        project_id = service_account_info.get("project_id")
        if not project_id:
            _LOGGER.error("Missing project_id in Firebase service account")
            return None, None

        now = int(time.time())
        payload = {
            "iss": client_email,
            "scope": "https://www.googleapis.com/auth/firebase.messaging",
            "aud": "https://oauth2.googleapis.com/token",
            "iat": now,
            "exp": now + 3600,
        }

        signer = rsa.RSASigner.from_string(private_key)
        assertion = jwt.encode(signer, payload)

        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": assertion,
                },
            ) as resp:
                result = await resp.json()
                if "access_token" not in result:
                    _LOGGER.error("Firebase OAuth error: %s", result)
                    return None, None
                return result["access_token"], project_id
    except OasiraAPIError as exc:
        _LOGGER.error("Failed to fetch Firebase config: %s", exc)
        return None, None
    except Exception as exc:
        _LOGGER.exception("Failed to refresh Firebase access token: %s", exc)
        return None, None
