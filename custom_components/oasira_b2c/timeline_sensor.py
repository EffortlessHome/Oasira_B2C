"""Timeline sensor for viewing activity timeline."""

from __future__ import annotations

import logging
from typing import Optional, List

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN, NAME
from .timeline_event import SIGNAL_TIMELINE_UPDATED, get_timeline_manager, TimelineEvent

_LOGGER = logging.getLogger(__name__)


class TimelineSensor(SensorEntity, RestoreEntity):
    """Sensor to display recent timeline events."""

    _attr_should_poll = False
    _attr_icon = "mdi:timeline"

    def __init__(self) -> None:
        """Initialize timeline sensor."""
        self._state = "clear"
        self._attributes: dict = {}
        self._last_event_id: Optional[str] = None

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return "Timeline Activity"

    @property
    def unique_id(self) -> str:
        """Return the unique ID of the sensor."""
        return "timeline_activity_sensor"

    @property
    def device_info(self) -> dict:
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, NAME)},
            "name": NAME,
            "manufacturer": NAME,
        }

    @property
    def state(self) -> str:
        """Return the current state."""
        return self._state

    @property
    def extra_state_attributes(self) -> dict:
        """Return extra state attributes."""
        return self._attributes

    async def async_added_to_hass(self) -> None:
        """Restore previous state."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_TIMELINE_UPDATED,
                self._handle_timeline_updated,
            )
        )
        # Load recent events on startup
        await self._update_recent_events()

    @callback
    def _handle_timeline_updated(self) -> None:
        """Refresh when timeline events change."""
        self.hass.async_create_task(self._update_recent_events())

    async def _update_recent_events(self) -> None:
        """Update sensor with recent events."""
        try:
            manager = await get_timeline_manager(self.hass)
            recent = manager.get_recent_events(limit=10)

            if not recent:
                self._state = "clear"
                self._attributes = {}
                return

            # State is most recent event type
            latest = recent[0]
            self._state = latest.event_type
            self._last_event_id = latest.event_id

            # Build attributes with recent events
            events_data = []
            for event in recent[:5]:
                events_data.append({
                    "event_id": event.event_id,
                    "event_type": event.event_type,
                    "timestamp": event.timestamp.isoformat(),
                    "entity_id": event.camera_entity_id,
                    "entity_name": event.camera_name,
                    "area_id": event.area_id,
                    "area_name": event.area_name,
                    "description": event.description,
                    "labels": event.labels,
                    "metadata": event.metadata,
                    "confidence": event.confidence,
                    "is_favorite": event.is_favorite,
                    "is_reviewed": event.is_reviewed,
                    "has_snapshot": event.snapshot_path is not None,
                    "has_video": event.video_clip_path is not None,
                    "snapshot_path": event.snapshot_path,
                    "video_clip_path": event.video_clip_path,
                })

            self._attributes = {
                "last_event_id": latest.event_id,
                "last_event_type": latest.event_type,
                "last_entity_id": latest.camera_entity_id,
                "last_entity_name": latest.camera_name,
                "last_area_id": latest.area_id,
                "last_area_name": latest.area_name,
                "last_description": latest.description,
                "last_labels": latest.labels,
                "last_metadata": latest.metadata,
                "last_has_snapshot": latest.snapshot_path is not None,
                "last_has_video": latest.video_clip_path is not None,
                "recent_events": events_data,
                "total_events_today": len([e for e in recent if e.timestamp.date() == dt_util.utcnow().date()]),
                "last_update": dt_util.utcnow().isoformat(),
            }
            self.async_write_ha_state()

        except Exception as e:
            _LOGGER.error("Failed to update timeline sensor: %s", e)

    async def async_update(self) -> None:
        """Update the sensor."""
        await self._update_recent_events()


class TimelineCameraSensor(SensorEntity):
    """Sensor for per-camera timeline summary."""

    def __init__(self, camera_entity_id: str, camera_name: str, area_id: Optional[str] = None) -> None:
        """Initialize camera timeline sensor."""
        self._camera_entity_id = camera_entity_id
        self._camera_name = camera_name
        self._area_id = area_id
        self._state = "clear"
        self._today_count = 0
        self._attributes: dict = {}

    @property
    def name(self) -> str:
        """Return the sensor name."""
        return f"Timeline {self._camera_name}"

    @property
    def unique_id(self) -> str:
        """Return unique ID."""
        return f"timeline_camera_{self._camera_entity_id.replace('.', '_')}"

    @property
    def device_info(self) -> dict:
        """Return device info."""
        return {
            "identifiers": {(DOMAIN, f"camera_{self._camera_entity_id}")},
            "name": self._camera_name,
            "manufacturer": NAME,
            "model": "Camera",
        }

    @property
    def state(self) -> str:
        """Return current state."""
        return self._state

    @property
    def extra_state_attributes(self) -> dict:
        """Return extra attributes."""
        return self._attributes

    @property
    def icon(self) -> str:
        """Return icon."""
        return "mdi:timeline"

    async def async_update(self) -> None:
        """Update sensor."""
        try:
            manager = await get_timeline_manager(self.hass)
            today = dt_util.utcnow().date()
            start = dt_util.datetime_to_timestamp(dt_util.start_of_local_day())
            end = dt_util.datetime_to_timestamp(dt_util.end_of_local_day(start))

            events = manager.get_timeline_for_camera(
                self._camera_entity_id,
                start_date=dt_util.utc_from_timestamp(start),
                end_date=dt_util.utc_from_timestamp(end),
                limit=100
            )

            self._today_count = len(events)
            if events:
                self._state = events[0].event_type
            else:
                self._state = "clear"

            self._attributes = {
                "events_today": self._today_count,
                "camera_entity_id": self._camera_entity_id,
                "area_id": self._area_id,
            }
        except Exception as e:
            _LOGGER.error("Failed to update camera timeline sensor: %s", e)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up timeline sensors."""
    async_add_entities([TimelineSensor()])

    # Add per-camera sensors for cameras that have motion detection
    # This will be populated based on available camera entities
    # For now, we create a single sensor that aggregates all cameras