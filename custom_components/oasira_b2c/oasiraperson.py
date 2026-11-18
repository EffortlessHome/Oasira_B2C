from __future__ import annotations

import json
import logging
from typing import Optional, Dict, Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import async_get as async_get_dev_reg
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, NAME

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

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return attributes for Home Assistant."""

        return {
            "oasira_type": "OasiraPerson",
            "email": self._email,
            "local_tracker": self._local_tracker_entity_id,
            "remote_tracker": self._remote_tracker_entity_id,
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

    async def async_added_to_hass(self):
        """Handle entity addition and restore previous state."""
        await super().async_added_to_hass()

        if (last_state := await self.async_get_last_state()) is None:
            _LOGGER.info("[OasiraPerson] No previous state to restore for %s", self._email)
            return

        attrs = last_state.attributes or {}
        self._local_tracker_entity_id = attrs.get("local_tracker")
        self._remote_tracker_entity_id = attrs.get("remote_tracker")

    async def async_update_health_data(self, health_data: Dict[str, Any]):
        """Update health data for this person."""
        _LOGGER.info("[OasiraPerson] Updating health data for %s", self._email)
        
        # Store the health data
        self._health_data = health_data
        
        # Trigger state update to reflect new health data timestamp in attributes
        if self.hass:
            self.async_write_ha_state()

    def __repr__(self):
        return f"<OasiraPerson email={self._email!r}>"