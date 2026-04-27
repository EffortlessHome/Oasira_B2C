"""Timeline event system for Oasira - HomeKit Secure Video-like capability."""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

TIMELINE_MEDIA_DIR = "www/snapshots"
TIMELINE_EVENTS_FILE = "timeline_events.json"
MAX_EVENTS_PER_DAY = 1000
SIGNAL_TIMELINE_UPDATED = f"{DOMAIN}_timeline_updated"


@dataclass

class TimelineEvent:
    """Represents a simple timeline event."""
    def __init__(self, event_id: str, timestamp: datetime, event_type: str, camera_entity_id: str, camera_name: str, area_id: str = None, area_name: str = None, description: str = None):
        self.event_id = event_id
        self.timestamp = timestamp
        self.event_type = event_type
        self.camera_entity_id = camera_entity_id
        self.camera_name = camera_name
        self.area_id = area_id
        self.area_name = area_name
        self.description = description

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp.isoformat(),
            "event_type": self.event_type,
            "camera_entity_id": self.camera_entity_id,
            "camera_name": self.camera_name,
            "area_id": self.area_id,
            "area_name": self.area_name,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TimelineEvent":
        return cls(
            event_id=data["event_id"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            event_type=data["event_type"],
            camera_entity_id=data["camera_entity_id"],
            camera_name=data["camera_name"],
            area_id=data.get("area_id"),
            area_name=data.get("area_name"),
            description=data.get("description"),
        )


class TimelineManager:
    """Manages timeline events and media storage."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize timeline manager."""
        self.hass = hass
        self._store = Store(hass, version=1, key=DOMAIN)
        self._events: List[TimelineEvent] = []
        # Save to /config/www/snapshots/<camera_name>/, accessible as /local/snapshots/<camera_name>/
        self._media_dir = Path(hass.config.path(TIMELINE_MEDIA_DIR))
        self._initialized = False

    async def async_initialize(self) -> None:
        """Initialize the timeline manager."""
        if self._initialized:
            return

        # Create media directory
        self._media_dir.mkdir(parents=True, exist_ok=True)

        # Load existing events
        data = await self._store.async_load()
        if data and "events" in data:
            self._events = [
                TimelineEvent.from_dict(e) for e in data["events"]
            ]

        # Clean up old events beyond retention period
        await self._cleanup_old_events()
        self._initialized = True
        _LOGGER.info("TimelineManager initialized with %d events", len(self._events))

    async def _cleanup_old_events(self, retention_days: int = 30) -> None:
        """Remove events older than retention period."""
        cutoff = dt_util.utcnow() - timedelta(days=retention_days)
        original_count = len(self._events)
        self._events = [e for e in self._events if e.timestamp > cutoff]
        if original_count > len(self._events):
            _LOGGER.info(
                "Cleaned up %d old timeline events",
                original_count - len(self._events)
            )

    async def _save_events(self) -> None:
        """Save events to persistent storage."""
        data = {
            "events": [e.to_dict() for e in self._events[-MAX_EVENTS_PER_DAY:]]
        }
        await self._store.async_save(data)

    def _notify_timeline_updated(self) -> None:
        """Notify listeners that timeline data changed."""
        async_dispatcher_send(self.hass, SIGNAL_TIMELINE_UPDATED)







    def get_timeline_for_camera(
        self,
        camera_entity_id: str,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        event_types: Optional[List[str]] = None,
        limit: int = 100,
    ) -> List[TimelineEvent]:
        """Get timeline events for a specific camera."""
        filtered = [
            e for e in self._events
            if e.camera_entity_id == camera_entity_id
        ]

        if start_date:
            filtered = [e for e in filtered if e.timestamp >= start_date]
        if end_date:
            filtered = [e for e in filtered if e.timestamp <= end_date]
        if event_types:
            filtered = [e for e in filtered if e.event_type in event_types]

        # Sort by timestamp descending
        filtered.sort(key=lambda e: e.timestamp, reverse=True)
        return filtered[:limit]

    def get_timeline_for_area(
        self,
        area_id: str,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        event_types: Optional[List[str]] = None,
        limit: int = 100,
    ) -> List[TimelineEvent]:
        """Get timeline events for a specific area."""
        filtered = [
            e for e in self._events if e.area_id == area_id
        ]

        if start_date:
            filtered = [e for e in filtered if e.timestamp >= start_date]
        if end_date:
            filtered = [e for e in filtered if e.timestamp <= end_date]
        if event_types:
            filtered = [e for e in filtered if e.event_type in event_types]

        filtered.sort(key=lambda e: e.timestamp, reverse=True)
        return filtered[:limit]

    def get_recent_events(self, limit: int = 50) -> List[TimelineEvent]:
        """Get most recent timeline events across all cameras."""
        sorted_events = sorted(self._events, key=lambda e: e.timestamp, reverse=True)
        return sorted_events[:limit]

    def mark_event_reviewed(self, event_id: str) -> bool:
        """Mark an event as reviewed."""
        for event in self._events:
            if event.event_id == event_id:
                event.is_reviewed = True
                return True
        return False

    def toggle_event_favorite(self, event_id: str) -> bool:
        """Toggle the favorite status of an event."""
        for event in self._events:
            if event.event_id == event_id:
                event.is_favorite = not event.is_favorite
                return True
        return False

    async def create_event(
        self,
        entity_id: str,
        entity_name: str,
        event_type: str,
        area_id: str = None,
        area_name: str = None,
        description: str = None,
    ) -> TimelineEvent:
        """Create a simple timeline event for any sensor or device."""
        event_id = str(uuid.uuid4())[:8]
        timestamp = dt_util.utcnow()
        event = TimelineEvent(
            event_id=event_id,
            timestamp=timestamp,
            event_type=event_type,
            camera_entity_id=entity_id,
            camera_name=entity_name,
            area_id=area_id,
            area_name=area_name,
            description=description,
        )
        self._events.append(event)
        await self._save_events()
        self._notify_timeline_updated()
        _LOGGER.info(
            "Created timeline event %s for entity %s: %s",
            event_id, entity_name, event_type
        )
        return event

    async def delete_event(self, event_id: str) -> bool:
        """Delete a timeline event."""
        for i, event in enumerate(self._events):
            if event.event_id == event_id:
                self._events.pop(i)
                await self._save_events()
                self._notify_timeline_updated()
                return True
        return False


# Global timeline manager instance
_timeline_manager: Optional[TimelineManager] = None


async def get_timeline_manager(hass: HomeAssistant) -> TimelineManager:
    """Get or create the timeline manager."""
    global _timeline_manager
    if _timeline_manager is None:
        _timeline_manager = TimelineManager(hass)
        await _timeline_manager.async_initialize()
    return _timeline_manager