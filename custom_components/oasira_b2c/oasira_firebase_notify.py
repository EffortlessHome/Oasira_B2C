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
    # Adding ATTR_DATA for rich content/embedded pictures
    ATTR_DATA,
)
from homeassistant.helpers import storage

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY = "oasira_firebase_tokens"
STORAGE_VERSION = 1

# IMPORTANT: Update this URL/project ID if necessary for the Oasira integration
FIREBASE_URL = (
    "https://fcm.googleapis.com/v1/projects/oasira_project_id/messages:send"
)

# NOTE: Replace 'oasira_project_id' with the actual Firebase project ID used by Oasira.
# Assuming the structure is similar to the working example.


async def async_get_service(hass, config, discovery_info=None):
    """Instantiates the Oasira Firebase Notify Service."""
    service_account_path = hass.config.path(
        "custom_components/oasira/firebase_service_account.json"
    )

    def load_creds():
        try:
            with open(service_account_path, "r") as f:
                return json.load(f)
        except FileNotFoundError:
            _LOGGER.error("Firebase service account file not found at %s", service_account_path)
            return {}

    creds = await hass.async_add_executor_job(load_creds)

    store = storage.Store(hass, STORAGE_VERSION, STORAGE_KEY)
    tokens = await store.async_load() or []

    return OasiraFirebaseNotifyService(hass, creds, tokens, store)


class OasiraFirebaseNotifyService(BaseNotificationService):
    """Service to send notifications via Firebase Cloud Messaging."""

    def __init__(self, hass, creds, tokens, store):
        self.hass = hass
        self.creds = creds
        self.tokens = tokens
        self.store = store
        self._access_token = None
        self._session: Optional[aiohttp.ClientSession] = None

    async def async_start(self):
        """Initializes the HTTP session."""
        self._session = aiohttp.ClientSession()
        _LOGGER.info("Oasira Firebase Notify Service started.")

    async def async_stop(self):
        """Closes the HTTP session."""
        await self._session.close()

    async def send_message(self, message: str, title: str, **kwargs):
        """
        Sends a notification message to all registered devices.
        Supports rich messages via extra data (e.g., images).
        """
        if not self.tokens:
            _LOGGER.warning("No registered FCM tokens. Notification skipped.")
            return

        # Extract optional rich data
        data = kwargs.get("data", {})
        # FCM supports rich notifications, including images, via the 'data' payload.
        # We pass all extra kwargs into the data payload for flexibility.
        if data:
            _LOGGER.debug("Sending notification with custom data: %s", data)

        token = await self._get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        # Iterate over tokens and send message to each
        for fcm_token in self.tokens:
            payload: Dict[str, Any] = {
                "message": {
                    "token": fcm_token,
                    "notification": {"title": title, "body": message},
                    # Use 'data' payload for custom fields, which is necessary for images
                    "data": data,
                }
            }
            
            try:
                async with self._session.post(
                    FIREBASE_URL, headers=headers, json=payload
                ) as resp:
                    if resp.status == 200:
                        _LOGGER.debug("Successfully sent notification to device.")
                    else:
                        text = await resp.text()
                        _LOGGER.error("Firebase push failed for token %s: Status %d, Text: %s", fcm_token, resp.status, text)
            except Exception as e:
                _LOGGER.error("Error sending Firebase push to token %s: %s", fcm_token, e)


    async def _get_access_token(self):
        """Obtains a new OAuth2 access token using JWT."""
        now = int(time.time())
        payload = {
            "iss": self.creds["client_email"],
            "scope": "https://www.googleapis.com/auth/firebase.messaging",
            "aud": "https://oauth2.googleapis.com/token",
            "iat": now,
            "exp": now + 3600,
        }

        # Ensure the private key is correctly loaded and used
        signer = rsa.RSASigner.from_string(self.creds["private_key"])
        assertion = jwt.encode(signer, payload)

        async with self._session.post(
            "https://oauth2.googleapis.com/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            }
        ) as resp:
            result = await resp.json()
            return result["access_token"]

    async def register_token(self, token: str):
        """Registers a new device token."""
        if token not in self.tokens:
            self.tokens.append(token)
            await self.store.async_save(self.tokens)
            _LOGGER.info("New token registered successfully.")

# Entry point function for Home Assistant to load the service
async def async_setup_entry(hass, config, entry):
    return await async_get_service(hass, config, entry)
