"""Platform for sensor integration."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import timedelta
from typing import Optional
from urllib.parse import quote

import aiohttp
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, SUN_EVENT_SUNRISE, SUN_EVENT_SUNSET
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.sun import get_astral_event_date
from homeassistant.util import dt as dt_util
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.device_registry import async_get as async_get_dev_reg
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)


from .const import (
    DOMAIN,
    NAME,
)


from .virtualpowersensor import (
    VirtualPowerSensor,
    VirtualPowerSensorAlwaysOn,
    TotalEnergySensor,
)


_LOGGER = logging.getLogger(__name__)


def _sanitize_unique_id(value: str) -> str:
    """Normalize a string for unique ID usage."""
    return re.sub(r"[^a-z0-9_]+", "_", value.lower()).strip("_")


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up entities."""

    async_add_entities([AverageHumiditySensor()])
    async_add_entities([AverageTemperatureSensor()])
    async_add_entities([VirtualIlluminanceSensor()])
    async_add_entities([HighTemperatureTomorrowSensor()])

    powerentities = []

    profiles = hass.data.get(DOMAIN, {}).get("virtual_power_profiles", [])
    for profile in profiles:
        name = profile.get("name") or profile.get("entity_id") or "virtual_device"
        wattage = profile.get("wattage", 0)
        always_on = bool(profile.get("always_on", False))
        profile_key = profile.get("key")

        if always_on:
            _LOGGER.debug(
                "Adding always-on virtual device: %s with wattage: %s",
                name,
                wattage,
            )
            powerentities.append(
                VirtualPowerSensorAlwaysOn(hass, name, wattage, profile_key)
            )
            continue

        entity_id = profile.get("entity_id")
        if not entity_id:
            _LOGGER.debug("Skipping virtual profile without entity_id: %s", profile)
            continue

        _LOGGER.debug(
            "Adding linked virtual device: %s (%s) with wattage: %s",
            name,
            entity_id,
            wattage,
        )
        powerentities.append(
            VirtualPowerSensor(hass, entity_id, wattage, name, profile_key)
        )

    async_add_entities(powerentities)
    async_add_entities([TotalEnergySensor(hass)])



async def _load_virtual_devices(hass, file_path):
    def read_file():
        with open(file_path, "r") as f:
            return json.load(f)

    return await hass.async_add_executor_job(read_file)



class AverageHumiditySensor(SensorEntity, RestoreEntity):
    """Representation of a sensor."""

    @property
    def device_info(self):
        """Return information about the device."""
        return {
            "identifiers": {(DOMAIN, NAME)},
            "name": NAME,
            "manufacturer": NAME,
        }

    def __init__(self) -> None:
        """Initialize the sensor."""
        self._state = ""

    @property
    def unique_id(self) -> str:
        """Return the unique ID of the sensor."""
        return self.name

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return "Average Humidity Sensor"

    @property
    def icon(self) -> str:
        """Return the icon of the sensor."""
        return "mdi:cloud-percent-outline"

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    def update(self) -> None:
        """Fetch new state data for the sensor.

        This is the only method that should fetch new data for Home Assistant.
        """

        group_entity_id = "group.humidity_sensors_group"

        # Ensure the group exists
        group_state = self.hass.states.get(group_entity_id)
        if not group_state:
            _LOGGER.debug(f"Group {group_entity_id} not found.")
            return

        # Get all entities in the group
        group_entities = group_state.attributes.get("entity_id", [])

        # Get all entities in the group
        group_entities = group_state.attributes.get("entity_id", [])

        numeric_values = []
        for entity_id in group_entities:
            entity_state = self.hass.states.get(entity_id)
            if entity_state:
                current_state = entity_state.state
                try:
                    # Attempt to convert the state to a float
                    numeric_value = float(current_state)
                    numeric_values.append(numeric_value)
                    _LOGGER.debug(
                        f"Entity {entity_id} has a numeric state of {numeric_value}"
                    )
                except ValueError:
                    # Non-numeric state, skip
                    _LOGGER.debug(
                        f"Entity {entity_id} state '{current_state}' is not numeric."
                    )
            else:
                _LOGGER.warning(f"Entity {entity_id} has no state available.")

        # Calculate the average if we have numeric values
        if numeric_values:
            average_value = sum(numeric_values) / len(numeric_values)
            self._state = round(average_value, 1)
            _LOGGER.debug(
                f"Average numeric state for group {group_entity_id}: {average_value}"
            )
        else:
            _LOGGER.debug(
                f"No numeric values found for entities in group {group_entity_id}."
            )
            self._state = -1


class AverageTemperatureSensor(SensorEntity, RestoreEntity):
    """Representation of a sensor."""

    @property
    def device_info(self):
        """Return information about the device."""
        return {
            "identifiers": {(DOMAIN, NAME)},
            "name": NAME,
            "manufacturer": NAME,
        }

    def __init__(self) -> None:
        """Initialize the sensor."""
        self._state = ""

    @property
    def unique_id(self) -> str:
        """Return the unique ID of the sensor."""
        return self.name

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return "Average Temperature Sensor"

    @property
    def icon(self) -> str:
        """Return the icon of the sensor."""
        return "mdi:thermometer"

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    def update(self) -> None:
        """Fetch new state data for the sensor.

        This is the only method that should fetch new data for Home Assistant.
        """

        group_entity_id = "group.temperature_sensors_group"

        # Ensure the group exists
        group_state = self.hass.states.get(group_entity_id)
        if not group_state:
            _LOGGER.debug(f"Group {group_entity_id} not found.")
            return

        # Get all entities in the group
        group_entities = group_state.attributes.get("entity_id", [])

        numeric_values = []
        for entity_id in group_entities:
            entity_state = self.hass.states.get(entity_id)
            if entity_state:
                current_state = entity_state.state
                try:
                    # Attempt to convert the state to a float
                    numeric_value = float(current_state)
                    numeric_values.append(numeric_value)
                    _LOGGER.debug(
                        f"Entity {entity_id} has a numeric state of {numeric_value}"
                    )
                except ValueError:
                    # Non-numeric state, skip
                    _LOGGER.debug(
                        f"Entity {entity_id} state '{current_state}' is not numeric."
                    )
            else:
                _LOGGER.warning(f"Entity {entity_id} has no state available.")

        # Calculate the average if we have numeric values
        if numeric_values:
            average_value = sum(numeric_values) / len(numeric_values)
            self._state = round(average_value, 1)
            _LOGGER.debug(
                f"Average numeric state for group {group_entity_id}: {average_value}"
            )
        else:
            _LOGGER.debug(
                f"No numeric values found for entities in group {group_entity_id}."
            )
            self._state = -1


class VirtualIlluminanceSensor(SensorEntity, RestoreEntity):
    """Representation of a sensor."""

    @property
    def device_info(self):
        """Return information about the device."""
        return {
            "identifiers": {(DOMAIN, NAME)},
            "name": NAME,
            "manufacturer": NAME,
        }

    def __init__(self) -> None:
        """Initialize the sensor."""
        self._state = 1000

    @property
    def unique_id(self) -> str:
        """Return the unique ID of the sensor."""
        return self.name

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return "VirtualIlluminanceSensor"

    @property
    def icon(self) -> str:
        """Return the icon of the sensor."""
        return "mdi:sun-wireless-outline"

    @property
    def device_class(self) -> str:
        """Return the device_class of the sensor."""
        return SensorDeviceClass.ILLUMINANCE

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    def update(self) -> None:
        """Fetch new state data for the sensor.

        This is the only method that should fetch new data for Home Assistant.
        """

        _LOGGER.debug("In virtual illuminance sensor update")

        self._state = 200

        sun_state = self.hass.states.get("sun.sun")
        if not sun_state:
            self._state = 1000
            _LOGGER.debug("In virtual illuminance sensor update. sun_state is None.")
            return  # Exit if sun entity isn't available

        if sun_state.state == "below_horizon":
            self._state = 100
            return

        sunrise_time = get_astral_event_date(self.hass, SUN_EVENT_SUNRISE)

        if not sunrise_time:
            self._state = 1000
            _LOGGER.debug("In virtual illuminance sensor update. sunrise time is None.")
            return  # Exit if sunrise time isn't available

        sunset_time = get_astral_event_date(self.hass, SUN_EVENT_SUNSET)

        if not sunset_time:
            self._state = 1000
            _LOGGER.debug("In virtual illuminance sensor update. sunset time is None.")
            return  # Exit if sunrise time isn't available

        # Convert sunrise/set times to datetime and calculate the time difference

        time_since_sunrise = dt_util.now() - sunrise_time
        secondssincesunrise = time_since_sunrise.total_seconds()

        time_until_sunset = sunset_time - dt_util.now()
        secondsuntilsunset = time_until_sunset.total_seconds()

        _LOGGER.debug(
            f"In virtual illuminance sensor seconds since sunrise: {secondssincesunrise}."
        )
        _LOGGER.debug(
            f"In virtual illuminance sensor seconds until sunset: {secondsuntilsunset}."
        )

        # are we closer to sunrise or sunset?
        if secondssincesunrise < secondsuntilsunset:
            if secondssincesunrise <= 500:
                self._state = 200
            elif secondssincesunrise <= 1000:
                self._state = 400
            elif secondssincesunrise <= 1500:
                self._state = 600
            elif secondssincesunrise <= 2000:
                self._state = 800
            elif secondssincesunrise <= 2700:
                self._state = 1000
            else:
                self._state = 1000
        elif secondsuntilsunset <= 500:
            self._state = 200
        elif secondsuntilsunset <= 1000:
            self._state = 400
        elif secondsuntilsunset <= 1500:
            self._state = 600
        elif secondsuntilsunset <= 2000:
            self._state = 800
        elif secondsuntilsunset <= 2700:
            self._state = 1000
        else:
            self._state = 1000


class HighTemperatureTomorrowSensor(SensorEntity, RestoreEntity):
    """Representation of a sensor."""

    @property
    def device_info(self):
        """Return information about the device."""
        return {
            "identifiers": {(DOMAIN, NAME)},
            "name": NAME,
            "manufacturer": NAME,
        }

    def __init__(self) -> None:
        """Initialize the sensor."""
        self._state = ""

    @property
    def unique_id(self) -> str:
        """Return the unique ID of the sensor."""
        return self.name

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return "High Temperature Tomorrow Sensor"

    @property
    def icon(self) -> str:
        """Return the icon of the sensor."""
        return "mdi:thermometer"

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    def update(self) -> None:
        """Fetch new state data for the sensor.

        This is the only method that should fetch new data for Home Assistant.
        """
        _LOGGER.debug("In high temp tomorrow forecast")

        forecasts = self.hass.services.call(
            "weather",
            "get_forecasts",
            {"type": "daily", "entity_id": "weather.forecast_home"},
            blocking=True,
            return_response=True,
        )

        _LOGGER.debug(f"In high temp tomorrow forecasts: {forecasts}")

        forecast = forecasts.get("weather.forecast_home").get("forecast")

        _LOGGER.debug(f"In high temp tomorrow forecast: {forecast}")

        if len(forecast) > 0:
            self._state = forecast[1]["temperature"]




