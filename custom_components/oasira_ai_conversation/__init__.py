"""The Oasira AI Conversation integration."""

from __future__ import annotations

import logging

import httpx

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import CONF_API_KEY, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_BASE_URL,
    CONF_TIMEOUT,
    DEFAULT_CONF_BASE_URL,
    DEFAULT_TIMEOUT,
    DOMAIN,
)
from .helpers import OllamaClient, get_authenticated_client
from .services import async_setup_services
from .template import async_setup_templates, async_unload_templates

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.AI_TASK, Platform.CONVERSATION]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type OasiraAIConfigEntry = ConfigEntry[OllamaClient]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up Oasira AI Conversation."""
    await async_migrate_integration(hass)
    await async_setup_services(hass, config)
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: OasiraAIConfigEntry
) -> bool:
    """Set up Oasira AI Conversation from a config entry."""

    try:
        client = await get_authenticated_client(
            hass=hass,
            base_url=entry.data.get(CONF_BASE_URL, DEFAULT_CONF_BASE_URL),
            timeout=entry.options.get(CONF_TIMEOUT, DEFAULT_TIMEOUT),
        )
    except httpx.ConnectError as err:
        _LOGGER.error("Could not connect to Ollama: %s", err)
        raise ConfigEntryNotReady(err) from err
    except httpx.TimeoutException as err:
        _LOGGER.error("Connection to Ollama timed out: %s", err)
        raise ConfigEntryNotReady(err) from err
    except Exception as err:
        _LOGGER.error("Error connecting to Ollama: %s", err)
        raise ConfigEntryNotReady(err) from err

    entry.runtime_data = client

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(update_listener))

    await async_setup_templates(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload Oasira AI."""
    await async_unload_templates(hass)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_migrate_integration(hass: HomeAssistant) -> None:
    """Migrate integration entry structure."""

    # Make sure we get enabled config entries first
    entries = sorted(
        hass.config_entries.async_entries(DOMAIN),
        key=lambda e: e.disabled_by is not None,
    )
    
    for entry in entries:
        # Migrate from version 1 to version 2
        if entry.version == 1:
            _LOGGER.warning(
                "Migrating Oasira AI Conversation config entry %s from version 1 to version 2",
                entry.entry_id,
            )
            subentry = ConfigSubentry(
                data=entry.options,
                subentry_type="conversation",
                title=entry.title,
                unique_id=None,
            )
            hass.config_entries.async_add_subentry(entry, subentry)
            hass.config_entries.async_update_entry(
                entry, title=entry.title, options={}, version=2
            )
        
        # Migrate from version 2 to version 3 (adds model selection)
        if entry.version == 2:
            _LOGGER.warning(
                "Migrating Oasira AI Conversation config entry %s from version 2 to version 3",
                entry.entry_id,
            )
            hass.config_entries.async_update_entry(
                entry, version=3
            )
