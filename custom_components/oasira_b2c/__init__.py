"""Oasira integration."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.components.notify import ATTR_DATA, ATTR_MESSAGE, ATTR_TITLE
from homeassistant.core import HomeAssistant, ServiceCall, callback
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

ATTR_TITLE = "title"
ATTR_DATA = "data"

from .auto_area import AutoArea

from .device_stream import OasiraHomeDeviceStreamer

from .const import (
    DOMAIN,
    LABELS,
    NAME,
)


from .deviceclassgroupsync import async_setup_devicegroup

_LOGGER = logging.getLogger(__name__)
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

    # This integration is now hub-only.
    is_hub = bool(entry.data.get("api_key"))
    if not is_hub:
        raise HomeAssistantError("Oasira now requires a hub enrollment code.")

    system_id = entry.data.get("system_id")
    customer_id = entry.data.get("customer_id")
    api_url = entry.data.get("api_url")
    api_key = entry.data.get("api_key")
    node_id = entry.data.get("node_id")
    node_name = entry.data.get("node_name")
    home_id = entry.data.get("home_id")
    home_name = entry.data.get("home_name")

    HASSComponent.set_hass(hass)
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].update(
        {
            "entry_id": entry.entry_id,
            "config_entry": entry,
            "virtual_power_store": virtual_power_store,
            "virtual_power_profiles": virtual_power_profiles,
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

    await hass.config_entries.async_forward_entry_setups(
        entry,
        [
            "switch",
            "binary_sensor",
            "sensor",
            
            
            "alarm_control_panel",
            "button",
        ],
    )

    register_services(hass)
    await async_setup_notification_service(hass)

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

    source_blueprints_dir = os.path.join(integration_dir, "blueprints")

    target_blueprints_dir = hass.config.path("blueprints")

    # Ensure destination directories exist
    os.makedirs(target_blueprints_dir, exist_ok=True)

    # Only copy files that are missing so user edits are preserved
    _copy_missing_files(source_blueprints_dir, target_blueprints_dir)


async def deploy_latest_config(hass: HomeAssistant):
    """Deploy the latest packaged blueprints."""
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
            
            
            "alarm_control_panel",
            "button",
        ],
    )

    if hass.services.has_service("notify", "oasira"):
        hass.services.async_remove("notify", "oasira")

    # Remove backward-compatible service aliases if present
    if hass.services.has_service(DOMAIN, "create_alert_service"):
        hass.services.async_remove(DOMAIN, "create_alert_service")

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
    ent_reg = er.async_get(hass)

    ent_reg.async_update_entity(entity_id, area_id=new_area)


async def loaddevicegroups(calldata) -> None:
    """Load device groups."""
    hass = HASSComponent.get_hass()
    await async_setup_devicegroup(hass)


async def handle_deploy_latest_config(call: ServiceCall) -> None:
    """Handle the service call."""
    hass = HASSComponent.get_hass()

    await deploy_latest_config(hass)


async def async_setup_notification_service(hass: HomeAssistant) -> None:
    """Register the notify.oasira service backed by the Oasira Home API."""
    domain_data = hass.data.get(DOMAIN, {})
    api_url = domain_data.get("api_url")
    api_key = domain_data.get("api_key")

    if not api_url or not api_key:
        _LOGGER.debug(
            "Oasira Home API credentials not available; notify.oasira not registered"
        )
        return

    async def handle_notify_service(call: ServiceCall) -> None:
        """Send a notification through the Oasira Home API."""
        current = hass.data.get(DOMAIN, {})
        current_api_url = current.get("api_url")
        current_api_key = current.get("api_key")
        if not current_api_url or not current_api_key:
            raise HomeAssistantError(
                "Oasira Home API credentials are unavailable for notify.oasira"
            )

        message = call.data.get(ATTR_MESSAGE) or call.data.get("message") or ""
        title = call.data.get(ATTR_TITLE) or call.data.get("title")
        data = call.data.get(ATTR_DATA) or call.data.get("data") or {}
        if not isinstance(data, dict):
            data = {"value": data}

        kind = str(data.get("kind") or "device").strip().lower()
        if kind not in {"security", "energy", "automation", "device"}:
            kind = "device"

        payload: dict[str, Any] = {
            "title": str(title) if title is not None else "Oasira",
            "message": str(message),
            "body": str(message),
            "kind": kind,
            "data": data,
        }

        node_id = current.get("node_id")
        home_id = current.get("home_id")
        if node_id:
            payload["node_id"] = node_id
        if home_id:
            payload["home_id"] = home_id

        session = async_get_clientsession(hass)
        endpoint = f"{current_api_url.rstrip('/')}/api/ingest/notifications"
        headers = {
            "Authorization": f"Bearer {current_api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with session.post(
                endpoint,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as response:
                if response.status in (401, 403):
                    entry = current.get("config_entry")
                    if entry is not None:
                        entry.async_start_reauth(hass)
                    raise HomeAssistantError(
                        "Oasira Home rejected the hub key; re-pair the hub to send notifications"
                    )
                if response.status >= 400:
                    body = await response.text()
                    raise HomeAssistantError(
                        f"Oasira Home notification failed (HTTP {response.status}): {body}"
                    )
        except aiohttp.ClientError as err:
            raise HomeAssistantError(
                f"Could not reach Oasira Home API: {err}"
            ) from err

    if not hass.services.has_service("notify", "oasira"):
        hass.services.async_register(
            "notify",
            "oasira",
            handle_notify_service,
            schema=vol.Schema(
                {
                    vol.Required(ATTR_MESSAGE): cv.string,
                    vol.Optional(ATTR_TITLE): cv.string,
                    vol.Optional(ATTR_DATA): dict,
                },
                extra=vol.ALLOW_EXTRA,
            ),
        )
        _LOGGER.info("Registered notify.oasira service using Oasira Home API")


# sampledata
# {
#    email:
#    token:
#    device_name: master_bedroom_tv
#    platform: android
# }
