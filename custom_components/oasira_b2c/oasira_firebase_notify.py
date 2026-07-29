import logging
import json
import aiohttp
import asyncio
import time
from google.auth import jwt
from google.auth.crypt import rsa
from homeassistant.components.notify import (
    BaseNotificationService,
    ATTR_TITLE,
    ATTR_MESSAGE,
    ATTR_DATA,
)
from homeassistant.helpers import storage
from typing import Optional, Dict, Any

from . import DOMAIN # Assuming DOMAIN is defined in __init__.py
from .oasira_api import OasiraAPIClient
from .const import FIREBASE_SCOPE
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY = "oasira_firebase_tokens"
STORAGE_VERSION = 1


class OasiraFirebaseNotifyService(BaseNotificationService):
    """Service to send notifications via Firebase Cloud Messaging."""

    def __init__(self, hass: HomeAssistant, project_id: str, tokens: list[str], store: storage.Store):
        self.hass = hass
        self.project_id = project_id
        self.tokens = tokens
        self.store = store
        self._access_token: Optional[str] = None
        self._session: Optional[aiohttp.ClientSession] = None

    async def async_start(self):
        """Initializes the HTTP session."""
        self._session = aiohttp.ClientSession()
        _LOGGER.info("Oasira Firebase Notify Service started.")

    async def async_stop(self):
        """Closes the HTTP session."""
        if self._session:
            await self._session.close()

    async def send_message(self, message: str, title: str, **kwargs):
        """
        Sends a notification message to all registered devices.
        Supports rich messages via extra data (e.g., images).
        """
        if not self.tokens:
            _LOGGER.warning("No registered FCM tokens. Notification skipped.")
            return

        data = kwargs.get("data", {})
        if data:
            _LOGGER.debug("Sending notification with custom data: %s", data)

        token = await self._get_access_token()
        if not token:
            _LOGGER.error("Failed to retrieve Firebase access token. Notification skipped.")
            return

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        fcm_url = f"https://fcm.googleapis.com/v1/projects/{self.project_id}/messages:send"

        # Iterate over tokens and send message to each
        for fcm_token in self.tokens:
            payload: Dict[str, Any] = {
                "message": {
                    "token": fcm_token,
                    "notification": {"title": title, "body": message},
                    "data": data,
                }
            }
            
            try:
                async with self._session.post(
                    fcm_url, headers=headers, json=payload
                ) as resp:
                    if resp.status == 200:
                        _LOGGER.debug("Successfully sent notification to device.")
                    else:
                        text = await resp.text()
                        _LOGGER.error("Firebase push failed for token %s: Status %d, Text: %s", fcm_token, resp.status, text)
            except Exception as e:
                _LOGGER.error("Error sending Firebase push to token %s: %s", fcm_token, e)


    async def _get_access_token(self) -> Optional[str]:
        """Obtains a new OAuth2 access token using JWT by fetching config from Oasira API."""
        hass = self.hass
        
        try:
            system_id = hass.data[DOMAIN].get("systemid")
            id_token = hass.data[DOMAIN].get("id_token")
            
            if not system_id or not id_token:
                _LOGGER.error("Missing system_id or id_token in configuration for token refresh.")
                return None

            async with OasiraAPIClient(system_id=system_id, id_token=id_token) as client:
                firebase_config = await client.get_firebase_config()
            
            google_firebase_raw = (
                firebase_config.get("Google_Firebase") if firebase_config else None
            )
            if not google_firebase_raw:
                _LOGGER.error("Missing Google_Firebase config from Oasira.")
                return None

            service_account_info = json.loads(google_firebase_raw)
            private_key = service_account_info["private_key"]
            client_email = service_account_info["client_email"]

            # --- OAuth2 JWT Flow ---
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
                        return None
                    return result["access_token"]
        
        except Exception as e:
            _LOGGER.exception("Failed to retrieve Firebase access token: %s", e)
            return None

    async def register_token(self, token: str):
        """Registers a new device token."""
        if token not in self.tokens:
            self.tokens.append(token)
            await self.store.async_save(self.tokens)
            _LOGGER.info("New token registered successfully.")

# Entry point function for Home Assistant to load the service
async def async_get_service(hass: HomeAssistant, config: Optional[Dict[str, Any]] = None, discovery_info=None):
    """Instantiates the Oasira Firebase Notify Service dynamically."""
    domain_data = hass.data.get(DOMAIN, {})
    
    # Retrieve stored tokens
    store = storage.Store(hass, STORAGE_VERSION, STORAGE_KEY)
    tokens = await store.async_load() or []

    # Retrieve Project ID (which must be available in the config/data loaded by __init__.py)
    project_id = domain_data.get("project_id")
    
    if not project_id:
        _LOGGER.error("Project ID not found in HA data. Cannot initialize Firebase service.")
        return None

    # Initialize the service
    service = OasiraFirebaseNotifyService(hass, project_id, tokens, store)
    return service


async def async_setup_entry(hass, config, entry):
    """Set up integration from a config entry."""
    # Use the dynamic getter
    service = await async_get_service(hass, config, entry)
    if service:
        await service.async_start()
        return service
    return None