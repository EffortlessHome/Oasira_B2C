"""Face recognition sensor for Oasira integration."""

from __future__ import annotations

import logging
import os
from typing import Any

try:
    import face_recognition
except ImportError:
    face_recognition = None

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util
from .const import DOMAIN, NAME

_LOGGER = logging.getLogger(__name__)


class PersonFaceRecognitionSensor(BinarySensorEntity, RestoreEntity):
    """Binary sensor to detect face recognition events."""

    _attr_should_poll = False
    _attr_icon = "mdi:face-recognition"

    def __init__(self, hass: HomeAssistant):
        """Initialize face recognition sensor."""
        self.hass = hass
        self._attr_name = "Face Recognition"
        self._attr_unique_id = "face_recognition_sensor"
        self._is_on = False
        self._last_recognized_person = "Unknown"
        self._camera_entity_id = "camera.living_room"
        self._person_dir = None

    @property
    def device_info(self) -> dict[str, Any]:
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, NAME)},
            "name": NAME,
            "manufacturer": NAME,
        }

    @property
    def is_on(self) -> bool:
        """Return True if a face was recognized."""
        return self._is_on

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra attributes."""
        return {
            "last_recognized_person": self._last_recognized_person,
            "requires_face_recognition_package": face_recognition is None,
        }

    async def async_added_to_hass(self) -> None:
        """Restore previous state on startup."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state:
            self._is_on = last_state.state == "on"
            if last_state.attributes:
                self._last_recognized_person = last_state.attributes.get(
                    "last_recognized_person", "Unknown"
                )