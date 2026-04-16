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
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

TIMELINE_MEDIA_DIR = "media/timeline"
TIMELINE_EVENTS_FILE = "timeline_events.json"
MAX_EVENTS_PER_DAY = 1000


@dataclass
class TimelineEvent:
    """Represents a timeline event with snapshot/video."""

    event_id: str
    timestamp: datetime
    event_type: str  # "person_detected", "motion", "vehicle", etc.
    camera_entity_id: str
    camera_name: str
    area_id: Optional[str] = None
    area_name: Optional[str] = None
    snapshot_path: Optional[str] = None
    video_clip_path: Optional[str] = None
    video_duration: Optional[int] = None  # seconds
    thumbnail_path: Optional[str] = None
    description: Optional[str] = None
    confidence: Optional[float] = None
    labels: List[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    is_favorite: bool = False
    is_reviewed: bool = False

    def to_dict(self) -> dict:
        """Convert to dictionary for storage."""
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp.isoformat(),
            "event_type": self.event_type,
            "camera_entity_id": self.camera_entity_id,
            "camera_name": self.camera_name,
            "area_id": self.area_id,
            "area_name": self.area_name,
            "snapshot_path": self.snapshot_path,
            "video_clip_path": self.video_clip_path,
            "video_duration": self.video_duration,
            "thumbnail_path": self.thumbnail_path,
            "description": self.description,
            "confidence": self.confidence,
            "labels": self.labels,
            "metadata": self.metadata,
            "is_favorite": self.is_favorite,
            "is_reviewed": self.is_reviewed,
        }

    @classmethod
    def from_dict(cls, data: dict) -> TimelineEvent:
        """Create from dictionary."""
        return cls(
            event_id=data["event_id"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            event_type=data["event_type"],
            camera_entity_id=data["camera_entity_id"],
            camera_name=data["camera_name"],
            area_id=data.get("area_id"),
            area_name=data.get("area_name"),
            snapshot_path=data.get("snapshot_path"),
            video_clip_path=data.get("video_clip_path"),
            video_duration=data.get("video_duration"),
            thumbnail_path=data.get("thumbnail_path"),
            description=data.get("description"),
            confidence=data.get("confidence"),
            labels=data.get("labels", []),
            metadata=data.get("metadata", {}),
            is_favorite=data.get("is_favorite", False),
            is_reviewed=data.get("is_reviewed", False),
        )


class TimelineManager:
    """Manages timeline events and media storage."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize timeline manager."""
        self.hass = hass
        self._store = Store(hass, version=1, key=DOMAIN)
        self._events: List[TimelineEvent] = []
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

    def get_camera_media_dir(self, camera_name: str) -> Path:
        """Get the media directory for a camera."""
        camera_dir = self._media_dir / camera_name.replace(" ", "_")
        camera_dir.mkdir(parents=True, exist_ok=True)
        return camera_dir

    async def create_person_detection_event(
        self,
        camera_entity_id: str,
        camera_name: str,
        snapshot_data: Optional[bytes] = None,
        video_clip_data: Optional[bytes] = None,
        video_duration: int = 5,
        area_id: Optional[str] = None,
        area_name: Optional[str] = None,
        confidence: Optional[float] = None,
        labels: Optional[List[str]] = None,
        description: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> TimelineEvent:
        """Create a timeline event for person detection."""
        event_id = str(uuid.uuid4())[:8]
        timestamp = dt_util.utcnow()

        # Save snapshot if provided
        snapshot_path = None
        thumbnail_path = None
        if snapshot_data:
            camera_dir = self.get_camera_media_dir(camera_name)
            snapshot_filename = f"{timestamp.strftime('%Y%m%d-%H%M%S')}_{event_id}_snapshot.jpg"
            snapshot_path = str(camera_dir / snapshot_filename)
            with open(snapshot_path, "wb") as f:
                f.write(snapshot_data)
            thumbnail_filename = f"{timestamp.strftime('%Y%m%d-%H%M%S')}_{event_id}_thumb.jpg"
            thumbnail_path = str(camera_dir / thumbnail_filename)
            # Thumbnails are same as snapshot for now (could optimize later)

        # Save video clip if provided
        video_clip_path = None
        if video_clip_data:
            camera_dir = self.get_camera_media_dir(camera_name)
            video_filename = f"{timestamp.strftime('%Y%m%d-%H%M%S')}_{event_id}_clip.mp4"
            video_clip_path = str(camera_dir / video_filename)
            with open(video_clip_path, "wb") as f:
                f.write(video_clip_data)

        event = TimelineEvent(
            event_id=event_id,
            timestamp=timestamp,
            event_type="person_detected",
            camera_entity_id=camera_entity_id,
            camera_name=camera_name,
            area_id=area_id,
            area_name=area_name,
            snapshot_path=snapshot_path,
            video_clip_path=video_clip_path,
            video_duration=video_duration if video_clip_data else None,
            thumbnail_path=thumbnail_path,
            description=description,
            confidence=confidence,
            labels=labels or [],
            metadata=metadata or {},
        )

        self._events.append(event)
        await self._save_events()

        _LOGGER.info(
            "Created timeline event %s for camera %s: person detected",
            event_id, camera_name
        )
        return event

    async def create_motion_event(
        self,
        camera_entity_id: str,
        camera_name: str,
        video_clip_data: Optional[bytes] = None,
        video_duration: int = 5,
        area_id: Optional[str] = None,
        area_name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> TimelineEvent:
        """Create a timeline event for motion detection."""
        event_id = str(uuid.uuid4())[:8]
        timestamp = dt_util.utcnow()

        video_clip_path = None
        if video_clip_data:
            camera_dir = self.get_camera_media_dir(camera_name)
            video_filename = f"{timestamp.strftime('%Y%m%d-%H%M%S')}_{event_id}_motion.mp4"
            video_clip_path = str(camera_dir / video_filename)
            with open(video_clip_path, "wb") as f:
                f.write(video_clip_data)

        event = TimelineEvent(
            event_id=event_id,
            timestamp=timestamp,
            event_type="motion",
            camera_entity_id=camera_entity_id,
            camera_name=camera_name,
            area_id=area_id,
            area_name=area_name,
            video_clip_path=video_clip_path,
            video_duration=video_duration if video_clip_data else None,
            description=description,
        )

        self._events.append(event)
        await self._save_events()

        return event

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

    async def delete_event(self, event_id: str) -> bool:
        """Delete a timeline event and its media files."""
        for i, event in enumerate(self._events):
            if event.event_id == event_id:
                # Delete media files
                for path_attr in ["snapshot_path", "video_clip_path", "thumbnail_path"]:
                    path = getattr(event, path_attr)
                    if path and os.path.exists(path):
                        try:
                            os.remove(path)
                        except Exception as e:
                            _LOGGER.warning("Failed to delete media file: %s", e)

                # Remove from list
                self._events.pop(i)
                await self._save_events()
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