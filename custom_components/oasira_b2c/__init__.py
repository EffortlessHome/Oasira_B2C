"""Oasira integration."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import shutil
import time
from typing import Any
from urllib.parse import quote

import aiohttp

from google.auth import jwt
from google.auth.crypt import rsa
import voluptuous as vol

from homeassistant.components import panel_custom
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.components import webhook
from homeassistant.helpers import entity_platform
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
    storage,
    label_registry as lr,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service import async_register_admin_service

from homeassistant.components.http import StaticPathConfig

ATTR_TITLE = "title"
ATTR_DATA = "data"

from .alarm_common import (
    async_cancelalarm,
    async_confirmpendingalarm,
    async_getalarmstatus,
)
from .auto_area import AutoArea

from .oasira import OasiraAPIClient, OasiraAPIError
from .auth_helper import ensure_valid_id_token, refresh_firebase_id_token, safe_api_call
from .device_stream import OasiraHomeDeviceStreamer

from .const import (
    DOMAIN,
    LABELS,
    NAME,
)


from .deviceclassgroupsync import async_setup_devicegroup
from .MotionSensorGrouper import MotionSensorGrouper
from .SecurityAlarmWebhook import SecurityAlarmWebhook



try:
    # Older versions (pre-2025)
    from homeassistant.components.device_tracker import SOURCE_TYPE_GPS
except ImportError:
    # Newer versions (2025+)
    SOURCE_TYPE_GPS = "gps"

from aiohttp import web

LOCATION_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): str,
        vol.Required("latitude"): float,
        vol.Required("longitude"): float,
        vol.Optional("accuracy"): float,
    }
)

_LOGGER = logging.getLogger(__name__)
LEGACY_DOMAIN = "oasira"

GOOGLE_OAUTH_URL = "https://oauth2.googleapis.com/token"
FIREBASE_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
FCM_URL = "https://fcm.googleapis.com/v1/projects/oasira-oauth/messages:send"
VIRTUAL_POWER_STORAGE_KEY = "oasira_virtual_power_profiles"
VIRTUAL_POWER_STORAGE_VERSION = 1


class HASSComponent:
    """Hasscomponent."""

    # Class-level property to hold the hass instance
    hass_instance = None

    @classmethod
    def set_hass(cls, hass: HomeAssistant) -> None:
        """Set Hass."""
        cls.hass_instance = hass

    @classmethod
    def get_hass(cls):
        """Get Hass."""
        return cls.hass_instance





async def handle_oasira_location_update(hass, webhook_id, request):
    """Register Oasira location update service."""

    _LOGGER.info("[Oasira] 📍 Handling location update webhook")
    _LOGGER.info("[Oasira] Request headers: %s", dict(request.headers))
    try:
        data = await request.json()
        _LOGGER.info("[Oasira] 📍 Location update payload: %s", data)
    except Exception as e:
        _LOGGER.error("[Oasira] ❌ Invalid JSON payload: %s", e)
        return web.Response(status=400, text="Invalid JSON")

    ####TODO: get user's email here and link this device tracker to them (local and online) #####

    device_name = data.get("device_name")
    device_id = data.get("device_id")
    lat = data.get("latitude")
    lon = data.get("longitude")
    accuracy = data.get("accuracy", 30.0)

    _LOGGER.info(
        "[Oasira] 📍 Parsed data - device_id: %s, lat: %s, lon: %s, accuracy: %s",
        device_id,
        lat,
        lon,
        accuracy,
    )

    if not device_id or lat is None or lon is None:
        _LOGGER.error(
            "[Oasira] ❌ Missing required fields - device_id: %s, lat: %s, lon: %s",
            device_id,
            lat,
            lon,
        )
        return web.Response(status=400, text="Missing required fields")

    device_id_new = (
        device_id.lower()
        .replace("@", "_")
        .replace(".", "_")
        .replace("-", "_")
        .replace("{", "")
        .replace("}", "")
    )
    entity_id = f"device_tracker.{device_id_new}"

    domain_data = hass.data.setdefault(DOMAIN, {})
    entry_id = domain_data.get("entry_id")
    device_registry = dr.async_get(hass)
    device_entry = device_registry.async_get_or_create(
        config_entry_id=entry_id,
        identifiers={(DOMAIN, device_id_new)},
        name=device_name or device_id_new,
        manufacturer=NAME,
        model="Mobile Device",
    )

    entity_registry = er.async_get(hass)
    entity_entry = entity_registry.async_get_or_create(
        domain="device_tracker",
        platform=DOMAIN,
        unique_id=f"{DOMAIN}_tracker_{device_id_new}",
        suggested_object_id=device_id_new,
        device_id=device_entry.id,
        original_name=device_name or device_id_new,
    )
    entity_id = entity_entry.entity_id

    _LOGGER.info("[Oasira] 📍 Creating/updating device tracker: %s", entity_id)

    # Update or create entity with immediate geofencing state determination
    # Calculate the state based on location coordinates right away
    state = "unknown"

    try:
        # Try to get home coordinates for immediate state calculation
        home_coords = None
        system_data = hass.data.get(DOMAIN, {})
        address_json = system_data.get("address_json")

        if address_json:
            if isinstance(address_json, str):
                address_data = json.loads(address_json)
            else:
                address_data = address_json

            home_lat = address_data.get("latitude")
            home_lon = address_data.get("longitude")

            if home_lat is not None and home_lon is not None:
                home_coords = (float(home_lat), float(home_lon))

        # Fallback to HA config coordinates
        if not home_coords:
            try:
                home_lat = hass.config.latitude
                home_lon = hass.config.longitude
                if home_lat is not None and home_lon is not None:
                    home_coords = (float(home_lat), float(home_lon))
            except:
                pass

        # Calculate distance and determine state
        if home_coords:
            home_lat, home_lon = home_coords

            # Earth's radius in meters
            R = 6371000

            # Convert degrees to radians
            lat1_rad = math.radians(home_lat)
            lon1_rad = math.radians(home_lon)
            lat2_rad = math.radians(lat)
            lon2_rad = math.radians(lon)

            # Haversine formula
            dlat = lat2_rad - lat1_rad
            dlon = lon2_rad - lon1_rad

            a = (
                math.sin(dlat / 2) ** 2
                + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
            )
            c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

            distance = R * c
            radius = 100.0  # 100 meter radius

            if distance <= radius:
                state = "home"
            else:
                state = "not_home"

    except Exception as e:
        _LOGGER.debug("[Oasira] Could not calculate geofence state: %s", e)
        state = "unknown"

    hass.states.async_set(
        entity_id,
        state,
        {
            "latitude": lat,
            "longitude": lon,
            "gps_accuracy": accuracy,
            "source_type": SOURCE_TYPE_GPS,
            "friendly_name": f" {device_name}",
        },
    )

    _LOGGER.info("[Oasira] ✅ Location update successful for %s", entity_id)
    return web.json_response({"status": "success", "message": "Location updated"})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up integration from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["entry_id"] = entry.entry_id

    virtual_power_store = storage.Store(
        hass,
        VIRTUAL_POWER_STORAGE_VERSION,
        VIRTUAL_POWER_STORAGE_KEY,
    )
    virtual_power_profiles = await virtual_power_store.async_load() or []

    # Hub entries are paired with an enrollment code (no Firebase login).
    # Legacy Firebase entries are still supported for existing installations.
    is_hub = bool(entry.data.get("api_key"))
    system_id = entry.data.get("system_id")
    customer_id = entry.data.get("customer_id")
    id_token = entry.data.get("id_token")

    if not is_hub:
        if not system_id:
            raise HomeAssistantError("System ID is missing in configuration.")
        if not customer_id:
            raise HomeAssistantError("Customer ID is missing in configuration.")

    HASSComponent.set_hass(hass)
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].update(
        {
            "entry_id": entry.entry_id,
            "config_entry": entry,
            "virtual_power_store": virtual_power_store,
            "virtual_power_profiles": virtual_power_profiles,
            "id_token": id_token,
            "refresh_token": entry.data.get("refresh_token"),
            "hub": is_hub,
        }
    )
    if is_hub:
        hass.data[DOMAIN].update(
            {
                "api_url": entry.data.get("api_url"),
                "api_key": entry.data.get("api_key"),
                "node_id": entry.data.get("node_id"),
                "node_name": entry.data.get("node_name"),
                "home_id": entry.data.get("home_id"),
                "home_name": entry.data.get("home_name"),
            }
        )
        # Confirm the hub key is still valid. A revoked key triggers the new
        # reauth flow, which asks for a fresh enrollment code.
        try:
            async with async_get_clientsession(hass).get(
                f"{entry.data['api_url']}/api/ingest/me",
                headers={"Authorization": f"Bearer {entry.data['api_key']}"},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as verify_response:
                if verify_response.status == 401:
                    raise ConfigEntryAuthFailed(
                        "The Oasira hub key was revoked; pair the hub again."
                    )
                if verify_response.status >= 400:
                    _LOGGER.warning(
                        "Oasira hub verification returned HTTP %s",
                        verify_response.status,
                    )
        except ConfigEntryAuthFailed:
            raise
        except (aiohttp.ClientError, TimeoutError) as verify_err:
            _LOGGER.warning(
                "Could not verify the Oasira hub key (integration will still load): %s",
                verify_err,
            )

    parsed_data = None
    plan_features = None
    if is_hub:
        # Cloud features backed by the legacy Firebase API are unavailable for
        # hub-paired entries; provide safe defaults so local features work.
        home_name = entry.data.get("home_name") or entry.data.get("home_id") or NAME
        parsed_data = {
            "fullname": home_name,
            "phonenumber": "",
            "emailaddress": "",
            "ha_token": "",
            "ha_url": "",
            "DaysHistoryToKeep": 30,
            "LowTemperatureWarning": 50,
            "HighTemperatureWarning": 85,
            "LowHumidityWarning": 30,
            "HighHumidityWarning": 60,
            "address_json": "{}",
            "systemphotolurl": "",
            "testmode": False,
            "additional_contacts_json": "[]",
            "instructions_json": "[]",
            "name": NAME,
        }

        # Stream device state / safety alerts to Oasira Home with the hub key.
        streamer = OasiraHomeDeviceStreamer(
            hass,
            entry.data["api_url"],
            entry.data["api_key"],
            entry.data["home_id"],
            entry.data["node_id"],
            on_auth_failed=lambda: entry.async_start_reauth(hass),
        )
        hass.data[DOMAIN]["device_streamer"] = streamer
        await streamer.async_start()
    else:
        if not await ensure_valid_id_token(hass):
            _LOGGER.warning(
                "Startup token validation failed; refresh or reauth may be required"
            )

        id_token = hass.data[DOMAIN].get("id_token")
        async with OasiraAPIClient(system_id=system_id, id_token=id_token) as api_client:
            async def _run_customer_system() -> Any:
                _id_token = hass.data[DOMAIN].get("id_token")
                if not _id_token:
                    raise OasiraAPIError("Missing id_token for customer/system lookup")
                async with OasiraAPIClient(system_id=system_id, id_token=_id_token) as client:
                    return await client.get_customer_and_system()

            try:
                parsed_data = await safe_api_call(hass, _run_customer_system)
            except OasiraAPIError as e:
                if "401" in str(e):
                    _LOGGER.warning("Customer/system lookup failed after token refresh: %s", e)
                    raise ConfigEntryAuthFailed(
                        f"Failed to fetch customer/system data after refresh: {e}"
                    ) from e
                _LOGGER.error("Failed to fetch customer/system data: %s", e)
                raise

            if parsed_data is None:
                raise HomeAssistantError("Could not retrieve customer/system data.")

            try:
                plan_features = await api_client.get_plan_features_by_system_id()
            except Exception as pf_exc:
                _LOGGER.warning("Failed to fetch plan features: %s", pf_exc)
                plan_features = None


    hass.data.setdefault(DOMAIN, {}).update({
        **(  # keep hub pairing details available to platforms
            {
                "hub": True,
                "api_url": entry.data.get("api_url"),
                "api_key": entry.data.get("api_key"),
                "node_id": entry.data.get("node_id"),
                "node_name": entry.data.get("node_name"),
                "home_id": entry.data.get("home_id"),
                "home_name": entry.data.get("home_name"),
            }
            if is_hub
            else {}
        ),
        "entry_id": entry.entry_id,
        "config_entry": entry,
        "virtual_power_store": virtual_power_store,
        "virtual_power_profiles": virtual_power_profiles,
        "device_streamer": hass.data[DOMAIN].get("device_streamer"),
        "fullname": parsed_data["fullname"],
        "phonenumber": parsed_data["phonenumber"],
        "emailaddress": parsed_data["emailaddress"],
        "ha_token": parsed_data["ha_token"],
        "ha_url": parsed_data["ha_url"],
        "email": parsed_data["emailaddress"],
        "username": parsed_data["emailaddress"],
        "systemid": system_id,
        "customerid": customer_id,
        "id_token": id_token,
        "refresh_token": entry.data.get("refresh_token"),
        "DaysHistoryToKeep": parsed_data["DaysHistoryToKeep"],
        "LowTemperatureWarning": parsed_data["LowTemperatureWarning"],
        "HighTemperatureWarning": parsed_data["HighTemperatureWarning"],
        "LowHumidityWarning": parsed_data["LowHumidityWarning"],
        "HighHumidityWarning": parsed_data["HighHumidityWarning"],
        "address_json": parsed_data["address_json"],
        "systemphotolurl": parsed_data["systemphotolurl"],
        "testmode": parsed_data["testmode"],
        "additional_contacts_json": parsed_data["additional_contacts_json"],
        "instructions_json": parsed_data["instructions_json"],
        "plan": parsed_data["name"],
        "plan_features": plan_features,
    })

    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, NAME)},
        name=NAME,
        manufacturer=NAME,
        model=NAME,
    )

    integration_dir = os.path.dirname(os.path.abspath(__file__))
    www_dir = os.path.join(integration_dir, "www", "oasira_b2c")
    try:
        await hass.http.async_register_static_paths(
            [StaticPathConfig("/oasira_b2c", www_dir, False)]
        )
    except Exception as exc:  # pragma: no cover - already registered on reload
        _LOGGER.debug("Static path registration skipped: %s", exc)

    for panel_name, module_file in (
        ("config", "config-panel.js"),
        ("area", "area-panel.js"),
        ("label", "label-panel.js"),
    ):
        try:
            await panel_custom.async_register_panel(
                hass,
                webcomponent_name=f"oasira-{panel_name}-panel",
                frontend_url_path=f"oasira-{panel_name}-panel",
                module_url=f"/oasira_b2c/{module_file}",
                sidebar_title=f"Oasira {panel_name.title()}",
                sidebar_icon="mdi:alpha-e-box-outline",
                require_admin=True,
            )
        except Exception as exc:  # pragma: no cover - already registered on reload
            _LOGGER.debug("Panel registration skipped for %s: %s", panel_name, exc)

    await hass.config_entries.async_forward_entry_setups(
        entry,
        [
            "switch",
            "binary_sensor",
            "sensor",
            "cover",
            "light",
            "alarm_control_panel",
            "button",
        ],
    )

    # Unregister if already registered
    location_webhook_id = hass.data.get(DOMAIN, {}).get("location_webhook_id")
    if location_webhook_id:
        webhook.async_unregister(hass, location_webhook_id)
    security_webhook_id = hass.data.get(DOMAIN, {}).get("security_alarm_webhook_id")
    if security_webhook_id:
        webhook.async_unregister(hass, security_webhook_id)

    security_webhook = SecurityAlarmWebhook(hass)
    await SecurityAlarmWebhook.async_setup_webhook(security_webhook)

    webhook_id = webhook.async_generate_id()
    hass.data.setdefault(DOMAIN, {})["location_webhook_id"] = webhook_id

    webhook.async_register(
        hass,
        DOMAIN,
        "Oasira Location Update",
        webhook_id,
        handle_oasira_location_update,
    )

    _LOGGER.info("[Oasira] Webhook registered: %s", webhook_id)

    register_services(hass)

    # Removed deploy_latest_config(hass) from initialization. Now triggered by button entity.
    label_registry = lr.async_get(hass)

    for desired in LABELS:
        try:
            label_registry.async_create(desired)
            _LOGGER.info("Created missing label: %s", desired)
        except ValueError:
            # Label already exists → ignore
            _LOGGER.info("Label already exists: %s", desired)

    async def after_home_assistant_started(event):
        """Call this function after Home Assistant has started."""
        # Create motion sensor groups
        try:
            grouper = MotionSensorGrouper(hass)
            await grouper.create_sensor_groups()
            await grouper.create_security_sensor_group()
        except Exception as e:
            _LOGGER.warning("Failed to create motion sensor groups: %s", e)


        await loaddevicegroups(None)

        # TODO: Update the link below with the actual add-on slug
        # notify_create(
        #    hass,
        #    title="Oasira Add-on Required",
        #    message=(
        #        "The Oasira integration needs the Oasira Add-on. "
        #        "Click [here](https://my.home-assistant.io/redirect/supervisor_addon/?addon=<your_slug>) to install it."
        #    ),
        # )

    # Listen for the 'homeassistant_started' event
    hass.bus.async_listen_once(
        EVENT_HOMEASSISTANT_STARTED, after_home_assistant_started
    )

    # Start Firebase token refresh task (refresh every 50 minutes, tokens expire in 60 minutes)
    async def refresh_firebase_token():
        """Periodically refresh the Firebase ID token."""
        # For hub-paired entries, token refresh is not needed (they use API keys)
        if is_hub:
            _LOGGER.debug("Hub-paired entry detected; skipping Firebase token refresh")
            return

        while True:
            try:
                _LOGGER.debug("Checking Firebase ID token validity...")

                refresh_token = hass.data[DOMAIN].get("refresh_token")
                if not refresh_token:
                    _LOGGER.warning(
                        "No refresh token available - cannot refresh Firebase token; "
                        "reauth may be required"
                    )
                    # Don't exit - keep trying in case token becomes available later
                    await asyncio.sleep(5 * 60)  # Retry in 5 minutes
                    continue

                if await refresh_firebase_id_token(hass):
                    _LOGGER.debug("✅ Firebase ID token refreshed successfully")
                else:
                    _LOGGER.warning("Failed to refresh Firebase token; will retry")

            except OasiraAPIError as e:
                _LOGGER.warning("Firebase token refresh error: %s", e)
                # Continue trying even if refresh fails
            except Exception as e:
                _LOGGER.exception("Unexpected error refreshing Firebase token: %s", e)
            finally:
                # Wait 50 minutes before refreshing (tokens expire in 60 minutes)
                await asyncio.sleep(50 * 60)

    # Schedule the refresh task after setup completes to avoid blocking startup
    async def schedule_refresh_task(*_):
        hass.async_create_task(refresh_firebase_token())
    hass.bus.async_listen_once("homeassistant_started", schedule_refresh_task)

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the integration when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)


def _copy_missing_files(source_dir: str, target_dir: str) -> None:
    """Copy files from source to target only when the target file is missing."""
    if not os.path.exists(source_dir):
        return

    for root, _, files in os.walk(source_dir):
        relative_root = os.path.relpath(root, source_dir)
        destination_root = (
            target_dir if relative_root == "." else os.path.join(target_dir, relative_root)
        )
        os.makedirs(destination_root, exist_ok=True)
        for filename in files:
            source_file = os.path.join(root, filename)
            destination_file = os.path.join(destination_root, filename)
            if not os.path.exists(destination_file):
                shutil.copy2(source_file, destination_file)


def _deploy_latest_config_sync(hass: HomeAssistant):
    """Synchronous helper for deploying config."""
    integration_dir = os.path.dirname(os.path.abspath(__file__))

    source_themes_dir = os.path.join(integration_dir, "themes")
    source_blueprints_dir = os.path.join(integration_dir, "blueprints")
    source_dir = os.path.join(integration_dir, "www/oasira_b2c")

    target_themes_dir = hass.config.path("themes")
    target_dir = hass.config.path("www/oasira_b2c")
    target_blueprints_dir = hass.config.path("blueprints")

    # Ensure destination directories exist
    os.makedirs(target_themes_dir, exist_ok=True)
    os.makedirs(target_dir, exist_ok=True)
    os.makedirs(target_blueprints_dir, exist_ok=True)

    # Only copy files that are missing so user edits are preserved
    _copy_missing_files(source_themes_dir, target_themes_dir)
    _copy_missing_files(source_blueprints_dir, target_blueprints_dir)
    _copy_missing_files(source_dir, target_dir)


async def deploy_latest_config(hass: HomeAssistant):
    """Deploy latest: theme, cards, blueprints, etc."""
    _LOGGER.info("[Oasira] Deploying latest configuration files...")
    await hass.async_add_executor_job(_deploy_latest_config_sync, hass)
    _LOGGER.info("[Oasira] Configuration deployment complete.")


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Unload a config entry."""

    streamer = hass.data.get(DOMAIN, {}).get("device_streamer")
    if streamer is not None:
        await streamer.async_stop()
        hass.data[DOMAIN].pop("device_streamer", None)

    await hass.config_entries.async_unload_platforms(
        entry,
        [
            "switch",
            "binary_sensor",
            "sensor",
            "cover",
            "light",
            "alarm_control_panel",
            "button",
        ],
    )
    # Remove backward-compatible service aliases if present
    if hass.services.has_service(DOMAIN, "create_alert_service"):
        hass.services.async_remove(DOMAIN, "create_alert_service")

    location_webhook_id = hass.data.get(DOMAIN, {}).get("location_webhook_id")
    if location_webhook_id:
        webhook.async_unregister(hass, location_webhook_id)
    security_webhook_id = hass.data.get(DOMAIN, {}).get("security_alarm_webhook_id")
    if security_webhook_id:
        webhook.async_unregister(hass, security_webhook_id)

    return True


async def async_init(hass: HomeAssistant, entry: ConfigEntry, auto_area: AutoArea):
    """Initialize component."""
    await asyncio.sleep(5)  # wait for all area devices to be initialized

    return True


async def add_label_to_entity(call: ServiceCall) -> None:
    """Add a label to an entity."""
    entity_id = call.data.get("entity_id")
    label = call.data.get("label")

    if not entity_id or not label:
        _LOGGER.error(
            "entity_id and label are required for add_label_to_entity service"
        )
        return

    hass = HASSComponent.get_hass()
    ent_reg = er.async_get(hass)
    entity_entry = ent_reg.async_get(entity_id)

    if not entity_entry:
        _LOGGER.error(f"Entity not found: {entity_id}")
        return

    new_labels = set(entity_entry.labels)
    new_labels.add(label)

    ent_reg.async_update_entity(entity_id, labels=new_labels)
    _LOGGER.info(f"Added label '{label}' to entity '{entity_id}'")


@callback
def register_services(hass: HomeAssistant) -> None:
    """Register Oasira services."""

    async_register_admin_service(hass, DOMAIN, "clean_motion_files", clean_motion_files)

    # Alarm/alert services only work for legacy Firebase entries, not enrolled hubs.
    if not hass.data.get(DOMAIN, {}).get("hub"):
        hass.services.async_register(DOMAIN, "create_event", create_event)
        hass.services.async_register(DOMAIN, "cancel_alarm", cancel_alarm)
        hass.services.async_register(DOMAIN, "get_alarm_status", get_alarm_status)
        hass.services.async_register(DOMAIN, "confirm_pending_alarm", confirm_pending_alarm)
        hass.services.async_register(DOMAIN, "create_alert", create_alert)

    async_register_admin_service(hass, DOMAIN, "update_entity", update_entity)

    async_register_admin_service(
        hass, DOMAIN, "deploy_latest_config", handle_deploy_latest_config
    )

    async_register_admin_service(
        hass,
        DOMAIN,
        "add_label_to_entity",
        add_label_to_entity,
        schema=vol.Schema(
            {vol.Required("entity_id"): cv.entity_id, vol.Required("label"): cv.string}
        ),
    )


    hass.services.async_register(
        DOMAIN,
        "add_virtual_power_device",
        add_virtual_power_device,
        schema=vol.Schema(
            {
                vol.Optional("entity_id"): cv.entity_id,
                vol.Optional("name"): cv.string,
                vol.Required("wattage"): vol.Coerce(float),
                vol.Optional("always_on", default=False): cv.boolean,
            }
        ),
    )

    hass.services.async_register(
        DOMAIN,
        "remove_virtual_power_device",
        remove_virtual_power_device,
        schema=vol.Schema(
            {
                vol.Optional("entity_id"): cv.entity_id,
                vol.Optional("name"): cv.string,
            }
        ),
    )

def _build_virtual_profile_key(entity_id: str | None, name: str | None, always_on: bool) -> str:
    """Build a stable key for a virtual power profile."""
    if not always_on and entity_id:
        return entity_id

    normalized = (name or "always_on_device").strip().lower().replace(" ", "_")
    return f"always_on:{normalized}"


async def _save_virtual_power_profiles(hass: HomeAssistant) -> None:
    """Persist virtual power profiles to Home Assistant storage."""
    domain_data = hass.data.get(DOMAIN, {})
    store = domain_data.get("virtual_power_store")
    profiles = domain_data.get("virtual_power_profiles", [])
    if store is not None:
        await store.async_save(profiles)


async def _reload_oasira_entry(hass: HomeAssistant) -> None:
    """Reload this integration entry so profile-based entities are rebuilt."""
    entry = hass.data.get(DOMAIN, {}).get("config_entry")
    if entry is None:
        return
    await hass.config_entries.async_reload(entry.entry_id)


async def add_virtual_power_device(call: ServiceCall) -> None:
    """Add or update a virtual power profile for total home usage estimation."""
    hass = HASSComponent.get_hass()
    domain_data = hass.data.setdefault(DOMAIN, {})
    profiles = list(domain_data.get("virtual_power_profiles", []))

    entity_id = call.data.get("entity_id")
    name = call.data.get("name")
    always_on = bool(call.data.get("always_on", False))
    wattage = float(call.data.get("wattage", 0.0))

    if wattage < 0:
        _LOGGER.error("wattage must be >= 0")
        return

    if not always_on and not entity_id:
        _LOGGER.error("entity_id is required when always_on is false")
        return

    if always_on and not name:
        _LOGGER.error("name is required when always_on is true")
        return

    profile_key = _build_virtual_profile_key(entity_id, name, always_on)
    profile_name = name or entity_id

    profile = {
        "key": profile_key,
        "entity_id": entity_id,
        "name": profile_name,
        "wattage": wattage,
        "always_on": always_on,
    }

    updated = False
    for idx, existing in enumerate(profiles):
        if existing.get("key") == profile_key:
            profiles[idx] = profile
            updated = True
            break

    if not updated:
        profiles.append(profile)

    domain_data["virtual_power_profiles"] = profiles
    await _save_virtual_power_profiles(hass)

    _LOGGER.info(
        "%s virtual power profile: %s (%s W)",
        "Updated" if updated else "Added",
        profile_name,
        wattage,
    )

    await _reload_oasira_entry(hass)


async def remove_virtual_power_device(call: ServiceCall) -> None:
    """Remove a virtual power profile by entity_id or name."""
    hass = HASSComponent.get_hass()
    domain_data = hass.data.setdefault(DOMAIN, {})
    profiles = list(domain_data.get("virtual_power_profiles", []))

    entity_id = call.data.get("entity_id")
    name = call.data.get("name")

    if not entity_id and not name:
        _LOGGER.error("entity_id or name is required")
        return

    normalized_name = (name or "").strip().lower().replace(" ", "_")
    before_count = len(profiles)
    profiles = [
        profile
        for profile in profiles
        if not (
            (entity_id and profile.get("entity_id") == entity_id)
            or (
                name
                and (
                    str(profile.get("name", "")).strip().lower().replace(" ", "_")
                    == normalized_name
                )
            )
        )
    ]

    if len(profiles) == before_count:
        _LOGGER.warning("No matching virtual power profile found to remove")
        return

    domain_data["virtual_power_profiles"] = profiles
    await _save_virtual_power_profiles(hass)
    _LOGGER.info("Removed virtual power profile")

    await _reload_oasira_entry(hass)


async def update_entity(call):
    """Handle the service call."""
    entity_id = call.data.get("entity_id")
    new_area = call.data.get("area_id")

    hass = HASSComponent.get_hass()
    ent_reg = entity_registry.async_get(hass)

    ent_reg.async_update_entity(entity_id, area_id=new_area)


async def loaddevicegroups(calldata) -> None:
    """Load device groups."""
    hass = HASSComponent.get_hass()
    await async_setup_devicegroup(hass)


async def create_event(call: ServiceCall) -> None:
    """Create event."""
    _LOGGER.info("create event calldata =%s", call.data)

    hass = HASSComponent.get_hass()

    entity_id = call.data.get("entity_id")
    if not entity_id:
        _LOGGER.error("entity_id is required for create_event service")
        return

    devicestate = hass.states.get(entity_id)
    sensor_device_class = None
    sensor_device_name = None

    if devicestate and devicestate.attributes.get("friendly_name"):
        sensor_device_name = devicestate.attributes["friendly_name"]

    if devicestate and devicestate.attributes.get("device_class"):
        sensor_device_class = devicestate.attributes["device_class"]

    if sensor_device_class is not None and sensor_device_name is not None:
        alarmstate = hass.data[DOMAIN].get("alarm_id")

        if alarmstate and alarmstate != "":
            alarmstatus = hass.data[DOMAIN].get("alarmstatus")

            if alarmstatus == "ACTIVE":
                alarmid = alarmstate
                _LOGGER.info("alarm id =%s", alarmid)

                # Call the API to create event
                systemid = hass.data[DOMAIN].get("systemid")
                id_token = hass.data[DOMAIN].get("id_token")

                event_data = {
                    "sensor_device_class": sensor_device_class,
                    "sensor_device_name": sensor_device_name,
                }

                _LOGGER.info("Calling create event API with payload: %s", event_data)

                if not await ensure_valid_id_token(hass):
                    _LOGGER.error("Cannot create event: invalid or expired id_token")
                    return None

                async def _run_create_event() -> Any:
                    id_token = hass.data[DOMAIN].get("id_token")
                    if not id_token:
                        raise OasiraAPIError("Missing id_token for create_event")
                    async with OasiraAPIClient(
                        system_id=systemid,
                        id_token=id_token,
                    ) as api_client:
                        return await api_client.create_event(alarmid, event_data)

                try:
                    result = await safe_api_call(hass, _run_create_event)
                    _LOGGER.info("API response content: %s", result)
                    return result
                except OasiraAPIError as e:
                    _LOGGER.error("Failed to create event: %s", e)
                    return None
            return None
        return None
    return None


# Keep old name for backward compatibility
async def createevent(calldata) -> None:
    """Create event (deprecated name)."""
    await create_event(calldata)


async def create_alert(call: ServiceCall) -> None:
    """Create alert."""
    _LOGGER.info("create alert calldata =%s", call.data)

    hass = HASSComponent.get_hass()
    alert_type = call.data.get("alert_type")
    alert_description = call.data.get("alert_description")
    status = call.data.get("status")

    if not alert_type or not alert_description or not status:
        _LOGGER.error(
            "alert_type, alert_description, and status are required for create_alert service"
        )
        return

    alert_data = {
        "alert_type": alert_type,
        "alert_description": alert_description,
        "status": status,
    }

    # Call the API to create alert
    systemid = hass.data[DOMAIN].get("systemid")
    id_token = hass.data[DOMAIN].get("id_token")

    _LOGGER.info("Calling alert API with payload: %s", alert_data)

    if not await ensure_valid_id_token(hass):
        _LOGGER.error("Cannot create alert: invalid or expired id_token")
        return None

    async def _run_create_alert() -> Any:
        id_token = hass.data[DOMAIN].get("id_token")
        if not id_token:
            raise OasiraAPIError("Missing id_token for create_alert")
        async with OasiraAPIClient(
            system_id=systemid,
            id_token=id_token,
        ) as api_client:
            return await api_client.create_alert(alert_data)

    try:
        result = await safe_api_call(hass, _run_create_alert)
        _LOGGER.info("API response content: %s", result)
        return result
    except OasiraAPIError as e:
        _LOGGER.error("Failed to create alert: %s", e)
        return None


# Keep old name for backward compatibility
async def createalert(calldata) -> None:
    """Create alert (deprecated name)."""
    await create_alert(calldata)


async def cancel_alarm(call: ServiceCall) -> None:
    """Cancel alarm."""
    hass = HASSComponent.get_hass()
    return await async_cancelalarm(hass)


# Keep old name for backward compatibility
async def cancelalarm(calldata) -> None:
    """Cancel alarm (deprecated name)."""
    await cancel_alarm(calldata)


async def get_alarm_status(call: ServiceCall) -> None:
    """Get alarm status."""
    hass = HASSComponent.get_hass()
    return await async_getalarmstatus(hass)


# Keep old name for backward compatibility
async def getalarmstatus(calldata) -> None:
    """Get alarm status (deprecated name)."""
    await get_alarm_status(calldata)


async def confirm_pending_alarm(call: ServiceCall) -> None:
    """Confirm pending alarm."""
    hass = HASSComponent.get_hass()
    return await async_confirmpendingalarm(hass)

def _clean_motion_files_sync(hass, age):
    """Synchronous helper to delete old snapshots."""
    snapshots_dir = "/media/snapshots"
    if not os.path.exists(snapshots_dir):
        _LOGGER.debug("Snapshots directory %s does not exist", snapshots_dir)
        return

    now = time.time()
    seconds_old = int(age) * 86400
    count = 0

    try:
        # Recursively walk through snapshots directory
        for root, _, files in os.walk(snapshots_dir):
            for name in files:
                file_path = os.path.join(root, name)
                try:
                    file_mtime = os.path.getmtime(file_path)
                    if now - file_mtime > seconds_old:
                        os.remove(file_path)
                        count += 1
                except Exception as e:
                    _LOGGER.error("Error deleting file %s: %s", file_path, e)

        if count > 0:
            _LOGGER.info("Successfully deleted %s old snapshots", count)
        else:
            _LOGGER.debug("No old snapshots found to delete")
    except Exception as e:
        _LOGGER.error("Error while cleaning snapshots: %s", e)


async def clean_motion_files(call: ServiceCall) -> None:
    """Delete snapshot files older than the specified number of days off the event loop."""
    hass = call.hass
    age = call.data.get("age", 30)

    try:
        age_int = int(age)
    except (ValueError, TypeError):
        _LOGGER.error("Invalid age value %s, using default 30 days", age)
        age_int = 30

    await hass.async_add_executor_job(_clean_motion_files_sync, hass, age_int)


# Keep old name for backward compatibility
async def cleanmotionfiles(calldata):
    """Execute the command to delete old snapshots (deprecated name)."""
    await clean_motion_files(calldata)


async def handle_deploy_latest_config(call: ServiceCall) -> None:
    """Handle the service call."""
    hass = HASSComponent.get_hass()

    await deploy_latest_config(hass)


# sampledata
# {
#    email:
#    token:
#    device_name: master_bedroom_tv
#    platform: android
# }

