"""Timeline services for recording snapshots and video clips on detection."""

from __future__ import annotations

import logging
import mimetypes
import os
from datetime import datetime, timedelta

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .timeline_event import get_timeline_manager

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


def _serialize_metadata_value(value: object) -> object:
    """Convert entity state metadata into storage-safe values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {
            str(key): _serialize_metadata_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_serialize_metadata_value(item) for item in value]
    return str(value)


def _build_entity_state_metadata(entity_state) -> dict:
    """Build a snapshot of the entity state for timeline event metadata."""
    if entity_state is None:
        return {}

    return {
        "entity_state": {
            "state": entity_state.state,
            "attributes": _serialize_metadata_value(dict(entity_state.attributes)),
            "last_changed": entity_state.last_changed.isoformat(),
            "last_updated": entity_state.last_updated.isoformat(),
        }
    }


def _infer_media_kind(file_path: str) -> str | None:
    """Infer whether a file is an image or video based on mime/ext."""
    mime_type, _ = mimetypes.guess_type(file_path)
    if mime_type:
        if mime_type.startswith("image/"):
            return "image"
        if mime_type.startswith("video/"):
            return "video"

    extension = os.path.splitext(file_path)[1].lower()
    if extension in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}:
        return "image"
    if extension in {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}:
        return "video"

    return None


def _resolve_media_path(hass: HomeAssistant, media_path: str) -> str:
    """Resolve a media path from service data into a readable filesystem path."""
    normalized_path = media_path.strip().strip('"').strip("'")

    if os.path.isabs(normalized_path):
        return normalized_path

    if normalized_path.startswith("/"):
        return hass.config.path(normalized_path.lstrip("/"))

    return hass.config.path(normalized_path)


async def _record_camera_clip(
    hass: HomeAssistant,
    camera_entity_id: str,
    camera_name: str,
    duration: int,
    remove_after_read: bool = True,
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

    with open(full_video_path, "rb") as file_handle:
        video_data = file_handle.read()

    if remove_after_read:
        try:
            os.remove(full_video_path)
        except OSError:
            _LOGGER.debug("Failed to remove temporary clip: %s", full_video_path)

    return video_data


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

CREATE_TIMELINE_EVENT_SCHEMA = vol.Schema({
    vol.Required("entity_id"): cv.string,
    vol.Optional("entity_name"): cv.string,
    vol.Required("event_type"): cv.string,
    vol.Optional("media_path"): cv.string,
    vol.Optional("confidence"): vol.Coerce(float),
    vol.Optional("area_name"): cv.string,
    vol.Optional("description"): cv.string,
})

SUMMARIZE_TIMELINE_PERIOD_SCHEMA = vol.Schema({
    vol.Optional("start_time"): cv.string,
    vol.Optional("end_time"): cv.string,
    vol.Optional("hours_back", default=24): vol.All(
        vol.Coerce(int), vol.Range(min=1, max=24 * 30)
    ),
    vol.Optional("include_entities", default=[]): [cv.string],
})


def _parse_service_datetime(value: str | None) -> datetime | None:
    """Parse an ISO datetime from service input and normalize to UTC."""
    if not value:
        return None

    parsed = dt_util.parse_datetime(value)
    if parsed is None:
        raise vol.Invalid(f"Invalid datetime: {value}")

    if parsed.tzinfo is None:
        parsed = dt_util.as_local(parsed)

    return dt_util.as_utc(parsed)


def _is_on_state(state: str) -> bool:
    """Return True when a state should be treated as active/on."""
    lowered = state.lower()
    return lowered not in {"off", "idle", "standby", "unavailable", "unknown", "none", "not_home"}


def _is_home_state(state: str) -> bool:
    """Return True when occupancy state indicates someone is home."""
    return state.lower() in {"home", "on", "present"}


def _hours(seconds: float) -> float:
    """Convert seconds to rounded hours."""
    return round(seconds / 3600, 2)

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
                remove_after_read=False,
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

    async def create_timeline_event(call: ServiceCall) -> ServiceResponse:
        """Create a generic timeline event for any sensor or device."""
        try:
            _LOGGER.debug("create_timeline_event called with data: %s", call.data)
            
            entity_id = call.data["entity_id"]
            _LOGGER.debug("entity_id: %s", entity_id)

            entity_state = hass.states.get(entity_id)
            entity_name = call.data.get("entity_name") or (
                entity_state.name if entity_state else entity_id
            )
            event_type = call.data["event_type"]
            media_path = call.data.get("media_path")
            area_name = call.data.get("area_name")
            description = call.data.get("description")
            confidence = call.data.get("confidence")
            metadata = _build_entity_state_metadata(entity_state)
            snapshot_data = None
            video_data = None

            if media_path:
                _LOGGER.debug("Processing media_path: %s", media_path)

                full_media_path = _resolve_media_path(hass, media_path)
                
                _LOGGER.debug("Resolved media_path to: %s", full_media_path)

                media_kind = _infer_media_kind(full_media_path)
                _LOGGER.debug("Inferred media kind: %s", media_kind)
                
                if media_kind is None:
                    error_msg = f"media_path must point to an image or video file: {full_media_path}"
                    _LOGGER.error(error_msg)
                    return {
                        "success": False,
                        "error": error_msg,
                    }

                try:
                    _LOGGER.debug("Reading media file: %s", full_media_path)
                    with open(full_media_path, "rb") as file_handle:
                        media_data = file_handle.read()
                    _LOGGER.debug("Read %d bytes from media file", len(media_data))
                except OSError as error:
                    error_msg = f"failed to read media_path {full_media_path}: {error}"
                    _LOGGER.error(error_msg)
                    return {
                        "success": False,
                        "error": error_msg,
                    }

                if media_kind == "image":
                    snapshot_data = media_data
                else:
                    video_data = media_data

            _LOGGER.debug("Getting timeline manager...")
            manager = await get_timeline_manager(hass)
            
            _LOGGER.debug("Creating event: entity_id=%s, entity_name=%s, event_type=%s, snapshot_data=%s, video_data=%s",
                         entity_id, entity_name, event_type, snapshot_data is not None, video_data is not None)

            event = await manager.create_event(
                entity_id=entity_id,
                entity_name=entity_name,
                event_type=event_type,
                snapshot_data=snapshot_data,
                video_clip_data=video_data,
                area_name=area_name,
                description=description,
                confidence=confidence,
                metadata=metadata,
            )

            _LOGGER.info("Successfully created timeline event: %s", event.event_id)
            
            return {
                "success": True,
                "event_id": event.event_id,
                "timestamp": event.timestamp.isoformat(),
                "has_snapshot": event.snapshot_path is not None,
                "has_video": event.video_clip_path is not None,
            }

        except Exception as e:
            import traceback
            error_msg = f"Failed to create event: {e}"
            _LOGGER.error(error_msg)
            _LOGGER.error("Traceback: %s", traceback.format_exc())
            return {"success": False, "error": error_msg}

    async def summarize_timeline_period(call: ServiceCall) -> ServiceResponse:
        """Summarize timeline data over a period and compute duration-based insights."""
        try:
            start_time = _parse_service_datetime(call.data.get("start_time"))
            end_time = _parse_service_datetime(call.data.get("end_time"))
            hours_back = call.data.get("hours_back", 24)
            include_entities = set(call.data.get("include_entities", []))

            now_utc = dt_util.utcnow()
            period_end = end_time or now_utc
            period_start = start_time or (period_end - timedelta(hours=hours_back))

            if period_start >= period_end:
                return {
                    "success": False,
                    "error": "start_time must be before end_time",
                }

            manager = await get_timeline_manager(hass)
            all_events = sorted(manager._events, key=lambda event: event.timestamp)  # noqa: SLF001

            tracked_events: list[tuple[str, datetime, str]] = []
            for event in all_events:
                entity_id = event.camera_entity_id
                if include_entities and entity_id not in include_entities:
                    continue

                if event.timestamp > period_end:
                    continue

                metadata = event.metadata or {}
                entity_state = metadata.get("entity_state", {})
                state_value = entity_state.get("state")
                if not isinstance(state_value, str):
                    continue

                tracked_events.append((entity_id, event.timestamp, state_value))

            if not tracked_events:
                return {
                    "success": True,
                    "period": {
                        "start": period_start.isoformat(),
                        "end": period_end.isoformat(),
                        "hours": _hours((period_end - period_start).total_seconds()),
                    },
                    "summary": "No timeline state events were found for the selected period.",
                    "metrics": {
                        "lights_on_hours": 0.0,
                        "appliances_running_hours": 0.0,
                        "someone_home_hours": 0.0,
                        "no_one_home_hours": 0.0,
                    },
                }

            events_by_entity: dict[str, list[tuple[datetime, str]]] = {}
            for entity_id, timestamp, state_value in tracked_events:
                events_by_entity.setdefault(entity_id, []).append((timestamp, state_value))

            lights_on_seconds = 0.0
            appliances_running_seconds = 0.0
            home_event_changes: list[tuple[datetime, str, str]] = []

            appliance_domains = {
                "switch",
                "fan",
                "climate",
                "humidifier",
                "dehumidifier",
                "vacuum",
                "water_heater",
            }

            per_entity_hours: dict[str, dict[str, float]] = {}

            for entity_id, states in events_by_entity.items():
                domain = entity_id.split(".", 1)[0] if "." in entity_id else ""
                states.sort(key=lambda item: item[0])

                baseline_state: str | None = None
                for timestamp, state_value in states:
                    if timestamp <= period_start:
                        baseline_state = state_value
                    else:
                        break

                state_points: list[tuple[datetime, str]] = []
                if baseline_state is not None:
                    state_points.append((period_start, baseline_state))

                for timestamp, state_value in states:
                    if period_start < timestamp <= period_end:
                        state_points.append((timestamp, state_value))

                if not state_points:
                    continue

                entity_light_seconds = 0.0
                entity_appliance_seconds = 0.0

                for index, (timestamp, state_value) in enumerate(state_points):
                    next_timestamp = (
                        state_points[index + 1][0]
                        if index + 1 < len(state_points)
                        else period_end
                    )

                    interval_seconds = (next_timestamp - timestamp).total_seconds()
                    if interval_seconds <= 0:
                        continue

                    if domain == "light" and _is_on_state(state_value):
                        lights_on_seconds += interval_seconds
                        entity_light_seconds += interval_seconds

                    if domain in appliance_domains and _is_on_state(state_value):
                        appliances_running_seconds += interval_seconds
                        entity_appliance_seconds += interval_seconds

                if domain in {"person", "device_tracker"}:
                    for timestamp, state_value in state_points:
                        home_event_changes.append((timestamp, entity_id, state_value))

                if entity_light_seconds or entity_appliance_seconds:
                    per_entity_hours[entity_id] = {
                        "light_on_hours": _hours(entity_light_seconds),
                        "appliance_running_hours": _hours(entity_appliance_seconds),
                    }

            someone_home_seconds = 0.0
            no_one_home_seconds = 0.0

            if home_event_changes:
                home_event_changes.sort(key=lambda item: item[0])
                home_state_by_entity: dict[str, str] = {}

                for timestamp, entity_id, state_value in home_event_changes:
                    if timestamp == period_start:
                        home_state_by_entity[entity_id] = state_value

                cursor_time = period_start
                for timestamp, entity_id, state_value in home_event_changes:
                    if timestamp < period_start:
                        home_state_by_entity[entity_id] = state_value
                        continue
                    if timestamp > period_end:
                        break

                    interval_seconds = (timestamp - cursor_time).total_seconds()
                    if interval_seconds > 0:
                        if any(_is_home_state(state) for state in home_state_by_entity.values()):
                            someone_home_seconds += interval_seconds
                        else:
                            no_one_home_seconds += interval_seconds

                    home_state_by_entity[entity_id] = state_value
                    cursor_time = timestamp

                final_interval_seconds = (period_end - cursor_time).total_seconds()
                if final_interval_seconds > 0:
                    if any(_is_home_state(state) for state in home_state_by_entity.values()):
                        someone_home_seconds += final_interval_seconds
                    else:
                        no_one_home_seconds += final_interval_seconds

            period_hours = _hours((period_end - period_start).total_seconds())
            lights_on_hours = _hours(lights_on_seconds)
            appliances_running_hours = _hours(appliances_running_seconds)
            someone_home_hours = _hours(someone_home_seconds)
            no_one_home_hours = _hours(no_one_home_seconds)

            summary = (
                f"From {period_start.isoformat()} to {period_end.isoformat()}, "
                f"lights were on for {lights_on_hours}h, appliances ran for {appliances_running_hours}h, "
                f"someone was home for {someone_home_hours}h, and no one was home for {no_one_home_hours}h."
            )

            return {
                "success": True,
                "period": {
                    "start": period_start.isoformat(),
                    "end": period_end.isoformat(),
                    "hours": period_hours,
                },
                "summary": summary,
                "metrics": {
                    "lights_on_hours": lights_on_hours,
                    "appliances_running_hours": appliances_running_hours,
                    "someone_home_hours": someone_home_hours,
                    "no_one_home_hours": no_one_home_hours,
                },
                "per_entity_hours": per_entity_hours,
                "events_analyzed": len(tracked_events),
            }
        except Exception as error:
            _LOGGER.error("Failed to summarize timeline period: %s", error, exc_info=True)
            return {"success": False, "error": str(error)}

    # Register services
    hass.services.async_register(DOMAIN, "capture_snapshot", capture_snapshot, CAPTURE_SNAPSHOT_SCHEMA)
    hass.services.async_register(DOMAIN, "record_video_clip", record_video_clip, RECORD_VIDEO_CLIP_SCHEMA)
    hass.services.async_register(
        DOMAIN,
        "create_timeline_event",
        create_timeline_event,
        CREATE_TIMELINE_EVENT_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        "summarize_timeline_period",
        summarize_timeline_period,
        SUMMARIZE_TIMELINE_PERIOD_SCHEMA,
    )

    _LOGGER.info("Timeline services registered")


def register_timeline_services(hass: HomeAssistant) -> None:
    """Register timeline services (sync wrapper for late registration)."""
    # Services are registered async in async_setup_services
    pass