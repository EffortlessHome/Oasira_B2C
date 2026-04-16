"""Timeline services for recording snapshots and video clips on detection."""

from __future__ import annotations

import logging
import base64
from typing import Optional, List

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .timeline_event import get_timeline_manager

_LOGGER = logging.getLogger(__name__)


async def async_setup_services(hass: HomeAssistant) -> None:
    """Set up timeline services."""

    async def capture_snapshot(call: ServiceCall) -> ServiceResponse:
        """Capture a snapshot from a camera and optionally save to timeline."""
        camera_entity_id = call.data["camera_entity_id"]
        save_to_timeline = call.data.get("save_to_timeline", True)
        event_type = call.data.get("event_type", "snapshot")
        description = call.data.get("description")
        labels = call.data.get("labels", [])
        area_id = call.data.get("area_id")
        area_name = call.data.get("area_name")

        # Get camera state to get camera name
        camera_state = hass.states.get(camera_entity_id)
        camera_name = camera_state.name if camera_state else camera_entity_id

        try:
            # Capture snapshot using camera service
            import datetime
            from homeassistant.util import dt as dt_util

            timestamp = dt_util.utcnow()
            snapshot_filename = f"{timestamp.strftime('%Y%m%d-%H%M%S')}_snapshot.jpg"
            snapshot_path = f"/media/snapshots/{camera_name.replace(' ', '_')}/{snapshot_filename}"

            # Call camera snapshot service
            await hass.services.async_call(
                "camera",
                "snapshot",
                {"entity_id": camera_entity_id, "filename": snapshot_path},
                blocking=True,
            )

            # If save_to_timeline, create timeline event
            if save_to_timeline:
                manager = await get_timeline_manager(hass)
                # Read the saved snapshot
                import os
                full_path = hass.config.path(snapshot_path.lstrip("/"))
                snapshot_data = None
                if os.path.exists(full_path):
                    with open(full_path, "rb") as f:
                        snapshot_data = f.read()

                event = await manager.create_person_detection_event(
                    camera_entity_id=camera_entity_id,
                    camera_name=camera_name,
                    snapshot_data=snapshot_data,
                    area_id=area_id,
                    area_name=area_name,
                    description=description,
                    labels=labels,
                )

                return {"success": True, "event_id": event.event_id, "snapshot_path": snapshot_path}

            return {"success": True, "snapshot_path": snapshot_path}

        except Exception as e:
            _LOGGER.error("Failed to capture snapshot: %s", e)
            return {"success": False, "error": str(e)}

    async def record_video_clip(call: ServiceCall) -> ServiceResponse:
        """Record a video clip from a camera and optionally save to timeline."""
        camera_entity_id = call.data["camera_entity_id"]
        duration = call.data.get("duration", 5)
        save_to_timeline = call.data.get("save_to_timeline", True)
        event_type = call.data.get("event_type", "motion")
        description = call.data.get("description")
        area_id = call.data.get("area_id")
        area_name = call.data.get("area_name")

        camera_state = hass.states.get(camera_entity_id)
        camera_name = camera_state.name if camera_state else camera_entity_id

        try:
            import datetime
            from homeassistant.util import dt as dt_util

            timestamp = dt_util.utcnow()
            video_filename = f"{timestamp.strftime('%Y%m%d-%H%M%S')}_clip.mp4"
            video_path = f"/media/clips/{camera_name.replace(' ', '_')}/{video_filename}"

            # Ensure directory exists
            clip_dir = hass.config.path(f"media/clips/{camera_name.replace(' ', '_')}")
            import os
            os.makedirs(clip_dir, exist_ok=True)

            full_video_path = hass.config.path(video_path.lstrip("/"))

            # Call camera record service
            await hass.services.async_call(
                "camera",
                "record",
                {
                    "entity_id": camera_entity_id,
                    "filename": full_video_path,
                    "duration": duration,
                    "lookback": 0,
                },
                blocking=True,
            )

            if save_to_timeline:
                manager = await get_timeline_manager(hass)
                video_data = None
                if os.path.exists(full_video_path):
                    with open(full_video_path, "rb") as f:
                        video_data = f.read()

                event = await manager.create_motion_event(
                    camera_entity_id=camera_entity_id,
                    camera_name=camera_name,
                    video_clip_data=video_data,
                    video_duration=duration,
                    area_id=area_id,
                    area_name=area_name,
                    description=description,
                )

                return {"success": True, "event_id": event.event_id, "video_path": video_path}

            return {"success": True, "video_path": video_path}

        except Exception as e:
            _LOGGER.error("Failed to record video clip: %s", e)
            return {"success": False, "error": str(e)}

    async def create_person_event(call: ServiceCall) -> ServiceResponse:
        """Create a person detection timeline event with snapshot and/or video."""
        camera_entity_id = call.data["camera_entity_id"]
        camera_name = call.data.get("camera_name", camera_entity_id)
        snapshot_b64 = call.data.get("snapshot_data")
        video_b64 = call.data.get("video_clip_data")
        video_duration = call.data.get("video_duration", 5)
        confidence = call.data.get("confidence", 1.0)
        labels = call.data.get("labels", ["person"])
        area_id = call.data.get("area_id")
        area_name = call.data.get("area_name")
        description = call.data.get("description")
        metadata = call.data.get("metadata", {})

        try:
            manager = await get_timeline_manager(hass)

            snapshot_data = None
            if snapshot_b64:
                try:
                    snapshot_data = base64.b64decode(snapshot_b64)
                except Exception as e:
                    _LOGGER.warning("Failed to decode snapshot data: %s", e)

            video_data = None
            if video_b64:
                try:
                    video_data = base64.b64decode(video_b64)
                except Exception as e:
                    _LOGGER.warning("Failed to decode video data: %s", e)

            event = await manager.create_person_detection_event(
                camera_entity_id=camera_entity_id,
                camera_name=camera_name,
                snapshot_data=snapshot_data,
                video_clip_data=video_data,
                video_duration=video_duration,
                area_id=area_id,
                area_name=area_name,
                confidence=confidence,
                labels=labels,
                description=description,
                metadata=metadata,
            )

            return {
                "success": True,
                "event_id": event.event_id,
                "timestamp": event.timestamp.isoformat(),
                "has_snapshot": event.snapshot_path is not None,
                "has_video": event.video_clip_path is not None,
            }

        except Exception as e:
            _LOGGER.error("Failed to create person event: %s", e)
            return {"success": False, "error": str(e)}

    async def get_timeline(call: ServiceCall) -> ServiceResponse:
        """Get timeline events."""
        camera_entity_id = call.data.get("camera_entity_id")
        area_id = call.data.get("area_id")
        event_types = call.data.get("event_types", [])
        start_date_str = call.data.get("start_date")
        end_date_str = call.data.get("end_date")
        limit = call.data.get("limit", 50)

        try:
            from homeassistant.util import dt as dt_util

            start_date = None
            end_date = None
            if start_date_str:
                parsed = dt_util.parse_datetime(start_date_str)
                if parsed:
                    start_date = dt_util.as_utc(parsed)
            if end_date_str:
                parsed = dt_util.parse_datetime(end_date_str)
                if parsed:
                    end_date = dt_util.as_utc(parsed)

            manager = await get_timeline_manager(hass)

            if camera_entity_id:
                events = manager.get_timeline_for_camera(
                    camera_entity_id,
                    start_date=start_date,
                    end_date=end_date,
                    event_types=event_types if event_types else None,
                    limit=limit,
                )
            elif area_id:
                events = manager.get_timeline_for_area(
                    area_id,
                    start_date=start_date,
                    end_date=end_date,
                    event_types=event_types if event_types else None,
                    limit=limit,
                )
            else:
                events = manager.get_recent_events(limit=limit)

            return {
                "success": True,
                "events": [e.to_dict() for e in events],
                "count": len(events),
            }

        except Exception as e:
            _LOGGER.error("Failed to get timeline: %s", e)
            return {"success": False, "error": str(e)}

    async def update_timeline_event(call: ServiceCall) -> ServiceResponse:
        """Update a timeline event."""
        event_id = call.data["event_id"]
        is_reviewed = call.data.get("is_reviewed")
        is_favorite = call.data.get("is_favorite")
        description = call.data.get("description")

        try:
            manager = await get_timeline_manager(hass)

            # Find and update event
            for event in manager._events:
                if event.event_id == event_id:
                    if is_reviewed is not None:
                        event.is_reviewed = is_reviewed
                    if is_favorite is not None:
                        event.is_favorite = is_favorite
                    if description is not None:
                        event.description = description
                    return {"success": True, "event_id": event_id}

            return {"success": False, "error": "Event not found"}

        except Exception as e:
            _LOGGER.error("Failed to update timeline event: %s", e)
            return {"success": False, "error": str(e)}

    async def delete_timeline_event(call: ServiceCall) -> ServiceResponse:
        """Delete a timeline event."""
        event_id = call.data["event_id"]

        try:
            manager = await get_timeline_manager(hass)
            success = await manager.delete_event(event_id)
            return {"success": success, "event_id": event_id}
        except Exception as e:
            _LOGGER.error("Failed to delete timeline event: %s", e)
            return {"success": False, "error": str(e)}

    # Register services
    hass.services.async_register(DOMAIN, "capture_snapshot", capture_snapshot, CAPTURE_SNAPSHOT_SCHEMA)
    hass.services.async_register(DOMAIN, "record_video_clip", record_video_clip, RECORD_VIDEO_CLIP_SCHEMA)
    hass.services.async_register(DOMAIN, "create_person_event", create_person_event, CREATE_PERSON_EVENT_SCHEMA)
    hass.services.async_register(DOMAIN, "get_timeline", get_timeline, GET_TIMELINE_SCHEMA)
    hass.services.async_register(DOMAIN, "update_timeline_event", update_timeline_event, UPDATE_EVENT_SCHEMA)
    hass.services.async_register(DOMAIN, "delete_timeline_event", delete_timeline_event, DELETE_EVENT_SCHEMA)

    _LOGGER.info("Timeline services registered")


def register_timeline_services(hass: HomeAssistant) -> None:
    """Register timeline services (sync wrapper for late registration)."""
    # Services are registered async in async_setup_services
    pass