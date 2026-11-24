from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from .const import DOMAIN, NAME

import logging

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities):
    async_add_entities(
        [
            DeployLatestConfigButton(hass, entry),
            OasiraAddonInstallButton(hass, entry),
            OasiraAddonStartButton(hass, entry),
        ]
    )


class DeployLatestConfigButton(ButtonEntity):
    _attr_name = "Deploy Latest Oasira Config"

    def __init__(self, hass: HomeAssistant, entry):
        self.hass = hass
        self._attr_unique_id = (
            f"deploy_latest_Oasira_config_button_{entry.entry_id}"
        )
        self._attr_config_entry_id = entry.entry_id

    @property
    def device_info(self):
        """Return information about the device."""
        return {
            "identifiers": {(DOMAIN, NAME)},
            "name": NAME,
            "manufacturer": NAME,
            "model": NAME,
        }

    async def async_press(self) -> None:
        from . import deploy_latest_config

        _LOGGER.info("[Oasira] Button pressed: Deploying latest config...")
        await deploy_latest_config(self.hass)


class OasiraAddonInstallButton(ButtonEntity):
    _attr_name = "Install Oasira Add-on"

    def __init__(self, hass: HomeAssistant, entry):
        self.hass = hass
        self._attr_unique_id = (
            f"install_oasira_addon_button_{entry.entry_id}"
        )
        self._attr_config_entry_id = entry.entry_id

    @property
    def device_info(self):
        """Return information about the device."""
        return {
            "identifiers": {(DOMAIN, NAME)},
            "name": NAME,
            "manufacturer": NAME,
            "model": NAME,
        }

    async def async_press(self) -> None:
        from . import async_install_oasira_addon

        _LOGGER.info(
            "[Oasira] Button pressed: Installing Oasira add-on..."
        )
        await async_install_oasira_addon(self.hass)


class OasiraAddonStartButton(ButtonEntity):
    _attr_name = "Start Oasira Add-on"

    def __init__(self, hass: HomeAssistant, entry):
        self.hass = hass
        self._attr_unique_id = (
            f"start_oasira_addon_button_{entry.entry_id}"
        )
        self._attr_config_entry_id = entry.entry_id

    @property
    def device_info(self):
        """Return information about the device."""
        return {
            "identifiers": {(DOMAIN, NAME)},
            "name": NAME,
            "manufacturer": NAME,
            "model": NAME,
        }

    async def async_press(self) -> None:
        from . import async_control_oasira_addon

        _LOGGER.info(
            "[Oasira] Button pressed: Starting Oasira add-on..."
        )
        await async_control_oasira_addon(self.hass, "start")
