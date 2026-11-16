"""Stream Home Assistant device state and safety alerts to Oasira Home.

Hub-paired entries authenticate with their revocable per-hub API key and post
to the Oasira Home Worker's /api/ingest endpoints. No Firebase account is used.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Callable

import aiohttp
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_FRIENDLY_NAME,
    ATTR_UNIT_OF_MEASUREMENT,
    EVENT_STATE_CHANGED,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import Event, HomeAssistant, State
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

TRACKED_DOMAINS = {
    "sensor", "binary_sensor", "switch", "light", "cover", "climate",
    "media_player", "camera", "lock", "fan", "humidifier", "water_heater",
}
ALERT_CLASSES = {
    "smoke", "gas", "heat", "moisture", "water", "problem", "safety",
    "motion", "door", "window", "opening", "vibration", "tamper",
}
HIGH_SEVERITY_CLASSES = {"smoke", "gas", "heat", "moisture", "water", "safety"}
MEDIUM_SEVERITY_CLASSES = {"motion", "door", "window", "opening", "vibration", "tamper"}
TRUE_STATES = {"on", "open", "home", "unlocked", "detected", "problems", "true", "motion"}
FALSE_STATES = {"off", "closed", "away", "locked", "clear", "false", "no_motion"}

BATCH_SIZE = 200
FLUSH_INTERVAL = timedelta(seconds=30)
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=15)


def _now_iso() -> str:
    return dt_util.utcnow().isoformat()


class OasiraHomeDeviceStreamer:
    """Batch device state into telemetry/alerts for the Oasira Home backend."""

    def __init__(
        self,
        hass: HomeAssistant,
        api_url: str,
        api_key: str,
        home_id: str,
        node_id: str,
        on_auth_failed: Callable[[], None] | None = None,
    ) -> None:
        self._hass = hass
        self._base_url = api_url.rstrip("/") + "/api/ingest"
        self._api_key = api_key
        self._home_id = home_id
        self._node_id = node_id
        self._on_auth_failed = on_auth_failed

        self._readings: dict[str, dict[str, Any]] = {}
        self._alerts: list[dict[str, Any]] = []
        self._active_alerts: dict[str, dict[str, Any]] = {}
        self._last_sent: dict[str, str] = {}
        self._area_cache: dict[str, str] = {}
        self._flush_lock = asyncio.Lock()
        self._unsub_bus = None
        self._unsub_timer = None
        self._auth_failed = False

    # -- lifecycle --------------------------------------------------------

    async def async_start(self) -> None:
        """Send an initial snapshot and start listening for state changes."""
        for state in self._hass.states.async_all():
            self._process_state(state, initial=True)
        self._unsub_bus = self._hass.bus.async_listen(
            EVENT_STATE_CHANGED, self._handle_state_changed
        )
        self._unsub_timer = async_track_time_interval(
            self._hass, self._async_tick, FLUSH_INTERVAL
        )
        await self._async_flush()
        _LOGGER.info(
            "Oasira Home device stream started (hub %s, home %s, %d initial readings)",
            self._node_id, self._home_id, len(self._readings),
        )

    async def async_stop(self) -> None:
        """Stop listening and make a final delivery attempt."""
        if self._unsub_bus:
            self._unsub_bus()
            self._unsub_bus = None
        if self._unsub_timer:
            self._unsub_timer()
            self._unsub_timer = None
        await self._async_flush()

    # -- state handling ---------------------------------------------------

    def _handle_state_changed(self, event: Event) -> None:
        new_state: State | None = event.data.get("new_state")
        if new_state is not None and self._is_tracked(new_state.entity_id):
            self._process_state(new_state, initial=False)

    def _is_tracked(self, entity_id: str) -> bool:
        if entity_id.split(".", 1)[0] not in TRACKED_DOMAINS:
            return False
        entry = er.async_get(self._hass).async_get(entity_id)
        if entry is not None:
            # Never echo this integration's own entities back to the server.
            if entry.platform == "oasira_b2c":
                return False
            if entry.disabled or entry.entity_category or entry.hidden_by:
                return False
        return True

    def _area_name(self, entity_id: str) -> str | None:
        if entity_id in self._area_cache:
            return self._area_cache[entity_id]
        area_name: str | None = None
        entry = er.async_get(self._hass).async_get(entity_id)
        area_id = entry.area_id if entry else None
        if area_id is None and entry is not None and entry.device_id:
            device = dr.async_get(self._hass).async_get(entry.device_id)
            area_id = device.area_id if device else None
        if area_id:
            area = ar.async_get(self._hass).async_get_area(area_id)
            area_name = area.name if area else area_id
        self._area_cache[entity_id] = area_name
        return area_name

    @staticmethod
    def _coerce_value(raw: str) -> Any:
        if raw == STATE_UNAVAILABLE:
            return "unavailable"
        if raw == STATE_UNKNOWN or not raw:
            return None
        lowered = raw.lower()
        if lowered in TRUE_STATES:
            return True
        if lowered in FALSE_STATES:
            return False
        try:
            return float(raw)
        except ValueError:
            return raw

    def _process_state(self, state: State, initial: bool) -> None:
        entity_id = state.entity_id
        value = self._coerce_value(state.state)
        if value is None:
            return

        attributes = state.attributes or {}
        device_class = attributes.get(ATTR_DEVICE_CLASS)
        if isinstance(device_class, str):
            sensor_type = device_class.strip().lower().replace(" ", "_").replace("-", "_")
        else:
            sensor_type = entity_id.split(".", 1)[0]

        signature = f"{state.state}|{attributes.get(ATTR_UNIT_OF_MEASUREMENT, '')}"
        if not initial and self._last_sent.get(entity_id) == signature:
            return
        self._last_sent[entity_id] = signature

        changed_at = state.last_changed.isoformat() if isinstance(state.last_changed, datetime) else _now_iso()
        unit = attributes.get(ATTR_UNIT_OF_MEASUREMENT)
        if unit is not None and not isinstance(unit, str):
            unit = str(unit)[:50]
        self._readings[entity_id] = {
            "entity_id": entity_id,
            "sensor_type": sensor_type,
            "value": value,
            "unit": unit,
            "name": attributes.get(ATTR_FRIENDLY_NAME) or entity_id,
            "location": self._area_name(entity_id),
            "recorded_at": changed_at,
            "metadata": {
                "domain": entity_id.split(".", 1)[0],
                "device_class": device_class,
                "hub_id": self._node_id,
                "home_id": self._home_id,
            },
        }
        self._process_alert(entity_id, state, device_class, initial)

    def _process_alert(
        self, entity_id: str, state: State, device_class: Any, initial: bool
    ) -> None:
        """Turn safety/security binary sensors into idempotent alerts."""
        is_on = state.state.lower() in TRUE_STATES or (
            isinstance(self._coerce_value(state.state), (int, float))
            and float(self._coerce_value(state.state)) > 0
        )
        active = self._active_alerts.get(entity_id)
        if is_on:
            if active is not None or initial:
                return
            alert_type = (
                device_class.strip().lower().replace(" ", "_")
                if isinstance(device_class, str) and device_class.strip()
                else entity_id.split(".", 1)[0]
            )
            if alert_type not in ALERT_CLASSES and not entity_id.startswith("binary_sensor."):
                return
            occurred_at = state.last_changed.isoformat() if isinstance(state.last_changed, datetime) else _now_iso()
            severity = (
                "critical" if alert_type in HIGH_SEVERITY_CLASSES
                else "high" if alert_type in MEDIUM_SEVERITY_CLASSES
                else "medium"
            )
            alert = {
                "external_id": f"{entity_id}:{occurred_at}",
                "type": alert_type,
                "severity": severity,
                "location": self._area_name(entity_id),
                "description": state.attributes.get(ATTR_FRIENDLY_NAME) or entity_id,
                "status": "active",
                "occurred_at": occurred_at,
            }
            self._active_alerts[entity_id] = alert
            self._alerts.append(alert)
        elif active is not None:
            # Clear the matching alert using the same external_id (idempotent).
            self._alerts.append({
                "external_id": active["external_id"],
                "type": active["type"],
                "severity": active["severity"],
                "location": active.get("location"),
                "description": active.get("description"),
                "status": "resolved",
                "occurred_at": _now_iso(),
            })
            self._active_alerts.pop(entity_id, None)

    # -- delivery ---------------------------------------------------------

    async def _async_tick(self, _now: datetime | None = None) -> None:
        await self._async_flush()

    async def _async_flush(self) -> None:
        if self._auth_failed:
            return
        async with self._flush_lock:
            if self._alerts:
                batch = self._alerts[:BATCH_SIZE]
                if await self._post("alerts", {"alerts": batch}):
                    self._alerts = self._alerts[BATCH_SIZE:]
                else:
                    # Keep the failed batch for the next tick (401 stops us entirely).
                    if not self._auth_failed:
                        self._alerts = batch + self._alerts[BATCH_SIZE:]
                    return
            if self._readings:
                items = list(self._readings.items())
                self._readings = {}
                for start in range(0, len(items), BATCH_SIZE):
                    batch = items[start:start + BATCH_SIZE]
                    if await self._post("telemetry", {"readings": [value for _, value in batch]}):
                        continue
                    if not self._auth_failed:
                        self._readings.update(batch + items[start + BATCH_SIZE:])
                    return

    async def _post(self, path: str, payload: dict[str, Any]) -> bool:
        session = async_get_clientsession(self._hass)
        try:
            async with session.post(
                f"{self._base_url}/{path}",
                json=payload,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=REQUEST_TIMEOUT,
            ) as response:
                if response.status in (401, 403):
                    self._auth_failed = True
                    _LOGGER.error(
                        "Oasira Home rejected this hub key; re-pair the hub in Home Assistant"
                    )
                    if self._on_auth_failed is not None:
                        self._on_auth_failed()
                    return False
                if response.status >= 400:
                    _LOGGER.warning(
                        "Oasira Home rejected a %s batch (HTTP %s); dropping it",
                        path, response.status,
                    )
                    # 4xx payloads will never succeed on retry; drop them.
                    return response.status < 500
                return True
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.warning("Oasira Home unreachable (%s); will retry", err)
            return False
