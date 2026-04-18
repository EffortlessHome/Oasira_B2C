"""Timeline services for recording snapshots and video clips on detection."""

from __future__ import annotations

import base64
import logging
import os

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .timeline_event import SIGNAL_TIMELINE_UPDATED, get_timeline_manager
from homeassistant.helpers.dispatcher import async_dispatcher_send

_LOGGER = logging.getLogger(__name__)


def _resolve_camera_entity_id(call: ServiceCall) -> str:
    """Resolve camera entity id from service data or target."""
    camera_entity_id = call.data.get("camera_entity_id")
    if camera_entity_id:
        return camera_entity_id

    target_entity_id = call.data.get("entity_id")
    if isinstance(target_entity_id, list):
        if not target_entity_id:
            raise vol.Invalid("entity_id target list is empty")
        return target_entity_id[0]
    if isinstance(target_entity_id, str):
        return target_entity_id

    raise vol.Invalid("camera_entity_id or target.entity_id is required")


def _normalize_labels(value: object, default: list[str] | None = None) -> list[str]:
    """Normalize labels from list/string input into a list of non-empty strings."""
    if value is None:
        return list(default or [])

    if isinstance(value, str):
        labels = [part.strip() for part in value.split(",")]
        return [label for label in labels if label]

    if isinstance(value, list):
        normalized: list[str] = []
        for item in value:
            if isinstance(item, str):
                if "," in item:
                    normalized.extend(
                        part.strip() for part in item.split(",") if part.strip()
                    )
                else:
                    item_str = item.strip()
                    if item_str:
                        normalized.append(item_str)
            elif item is not None:
                item_str = str(item).strip()
                if item_str:
                    normalized.append(item_str)
        return normalized

    value_str = str(value).strip()
    return [value_str] if value_str else list(default or [])


async def _record_camera_clip(
    hass: HomeAssistant,
    camera_entity_id: str,
    camera_name: str,
    duration: int,
) -> bytes | None:
    """Record a temporary clip from a camera and return its bytes."""
    from homeassistant.util import dt as dt_util

    timestamp = dt_util.utcnow()
    camera_slug = camera_name.replace(" ", "_")
    video_filename = f"{timestamp.strftime('%Y%m%d-%H%M%S')}_clip.mp4"
    clip_dir = hass.config.path(f"media/clips/{camera_slug}")
    os.makedirs(clip_dir, exist_ok=True)

    full_video_path = hass.config.path(f"media/clips/{camera_slug}/{video_filename}")

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

    if not os.path.exists(full_video_path):
        return None

    try:
        with open(full_video_path, "rb") as file_handle:
            return file_handle.read()
    finally:
        try:
            os.remove(full_video_path)
        except OSError:
            _LOGGER.debug("Failed to remove temporary clip: %s", full_video_path)


# Schema definitions for timeline services
CAPTURE_SNAPSHOT_SCHEMA = vol.Schema({
    vol.Optional("camera_entity_id"): cv.string,
    vol.Optional("entity_id"): vol.Any(cv.string, [cv.string]),
    vol.Optional("save_to_timeline", default=True): cv.boolean,
    vol.Optional("event_type", default="snapshot"): cv.string,
    vol.Optional("description"): cv.string,
    vol.Optional("labels", default=[]): cv.ensure_list,
    vol.Optional("area_id"): cv.string,
    vol.Optional("area_name"): cv.string,
})

RECORD_VIDEO_CLIP_SCHEMA = vol.Schema({
    vol.Optional("camera_entity_id"): cv.string,
    vol.Optional("entity_id"): vol.Any(cv.string, [cv.string]),
    vol.Optional("duration", default=5): cv.positive_int,
    vol.Optional("save_to_timeline", default=True): cv.boolean,
    vol.Optional("event_type", default="motion"): cv.string,
    vol.Optional("description"): cv.string,
    vol.Optional("area_id"): cv.string,
    vol.Optional("area_name"): cv.string,
})

CREATE_PERSON_EVENT_SCHEMA = vol.Schema({
    vol.Optional("camera_entity_id"): cv.string,
    vol.Optional("entity_id"): vol.Any(cv.string, [cv.string]),
    vol.Optional("camera_name"): cv.string,
    vol.Optional("snapshot_data"): cv.string,
    vol.Optional("video_clip_data"): cv.string,
    vol.Optional("video_duration", default=5): cv.positive_int,
    vol.Optional("confidence", default=1.0): vol.Coerce(float),
    vol.Optional("labels", default=["person"]): cv.ensure_list,
    vol.Optional("area_id"): cv.string,
    vol.Optional("area_name"): cv.string,
    vol.Optional("description"): cv.string,
    vol.Optional("metadata", default={}): dict,
})

UPDATE_EVENT_SCHEMA = vol.Schema({
    vol.Required("event_id"): cv.string,
    vol.Optional("is_reviewed"): cv.boolean,
    vol.Optional("is_favorite"): cv.boolean,
    vol.Optional("description"): cv.string,
})

CREATE_TIMELINE_EVENT_SCHEMA = vol.Schema({
    vol.Required("entity_id"): cv.string,
    vol.Optional("entity_name"): cv.string,
    vol.Required("event_type"): cv.string,
    vol.Optional("snapshot_data"): cv.string,
    vol.Optional("video_clip_data"): cv.string,
    vol.Optional("video_duration", default=5): cv.positive_int,
    vol.Optional("labels", default=[]): cv.ensure_list,
    vol.Optional("area_id"): cv.string,
    vol.Optional("area_name"): cv.string,
    vol.Optional("description"): cv.string,
    vol.Optional("metadata", default={}): dict,
})

async def async_setup_services(hass: HomeAssistant) -> None:
    """Set up timeline services."""

    async def capture_snapshot(call: ServiceCall) -> ServiceResponse:
        """Capture a snapshot from a camera and optionally save to timeline."""
        camera_entity_id = _resolve_camera_entity_id(call)
        save_to_timeline = call.data.get("save_to_timeline", True)
        event_type = call.data.get("event_type", "snapshot")
        description = call.data.get("description")
        labels = _normalize_labels(call.data.get("labels"), default=[])
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
        camera_entity_id = _resolve_camera_entity_id(call)
        duration = call.data.get("duration", 5)
        save_to_timeline = call.data.get("save_to_timeline", True)
        event_type = call.data.get("event_type", "motion")
        description = call.data.get("description")
        area_id = call.data.get("area_id")
        area_name = call.data.get("area_name")

        camera_state = hass.states.get(camera_entity_id)
        camera_name = camera_state.name if camera_state else camera_entity_id

        try:
            from homeassistant.util import dt as dt_util

            timestamp = dt_util.utcnow()
            video_filename = f"{timestamp.strftime('%Y%m%d-%H%M%S')}_clip.mp4"
            video_path = f"/media/clips/{camera_name.replace(' ', '_')}/{video_filename}"
            full_video_path = hass.config.path(video_path.lstrip("/"))

            video_data = await _record_camera_clip(
                hass,
                camera_entity_id,
                camera_name,
                duration,
            )

            if save_to_timeline:
                manager = await get_timeline_manager(hass)

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
        camera_entity_id = _resolve_camera_entity_id(call)
        camera_state = hass.states.get(camera_entity_id)
        camera_name = call.data.get("camera_name") or (
            camera_state.name if camera_state else camera_entity_id
        )
        snapshot_b64 = call.data.get("snapshot_data")
        video_b64 = call.data.get("video_clip_data")
        video_duration = call.data.get("video_duration", 5)
        confidence = call.data.get("confidence", 1.0)
        labels = _normalize_labels(call.data.get("labels"), default=["person"])
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
            else:
                try:
                    video_data = await _record_camera_clip(
                        hass,
                        camera_entity_id,
                        camera_name,
                        video_duration,
                    )
                except Exception as e:
                    _LOGGER.warning(
                        "Failed to auto-record clip for person event on %s: %s",
                        camera_entity_id,
                        e,
                    )

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
                    async_dispatcher_send(hass, SIGNAL_TIMELINE_UPDATED)
                    return {"success": True, "event_id": event_id}

            return {"success": False, "error": "Event not found"}

        except Exception as e:
            _LOGGER.error("Failed to update timeline event: %s", e)
            return {"success": False, "error": str(e)}

    async def create_timeline_event(call: ServiceCall) -> ServiceResponse:
        """Create a generic timeline event for any sensor or device."""
        entity_id = call.data["entity_id"]
        entity_name = call.data.get("entity_name", entity_id)
        event_type = call.data["event_type"]
        snapshot_b64 = call.data.get("snapshot_data")
        video_b64 = call.data.get("video_clip_data")
        video_duration = call.data.get("video_duration", 5)
        labels = _normalize_labels(call.data.get("labels"), default=[])
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

            event = await manager.create_event(
                entity_id=entity_id,
                entity_name=entity_name,
                event_type=event_type,
                snapshot_data=snapshot_data,
                video_clip_data=video_data,
                video_duration=video_duration,
                area_id=area_id,
                area_name=area_name,
                description=description,
                labels=labels,
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
            _LOGGER.error("Failed to create event: %s", e)
            return {"success": False, "error": str(e)}

    # Register services
    hass.services.async_register(DOMAIN, "capture_snapshot", capture_snapshot, CAPTURE_SNAPSHOT_SCHEMA)
    hass.services.async_register(DOMAIN, "record_video_clip", record_video_clip, RECORD_VIDEO_CLIP_SCHEMA)
    hass.services.async_register(DOMAIN, "create_person_event", create_person_event, CREATE_PERSON_EVENT_SCHEMA)
    hass.services.async_register(
        DOMAIN,
        "create_timeline_event",
        create_timeline_event,
        CREATE_TIMELINE_EVENT_SCHEMA,
    )
    hass.services.async_register(DOMAIN, "update_timeline_event", update_timeline_event, UPDATE_EVENT_SCHEMA)

    _LOGGER.info("Timeline services registered")


def register_timeline_services(hass: HomeAssistant) -> None:
    """Register timeline services (sync wrapper for late registration)."""
    # Services are registered async in async_setup_services
    pass