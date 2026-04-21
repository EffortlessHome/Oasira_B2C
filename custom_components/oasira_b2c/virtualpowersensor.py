"""Virtual power sensors for approximating device/home power usage."""

from __future__ import annotations

import logging
from random import uniform
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, NAME

_LOGGER = logging.getLogger(__name__)


class VirtualPowerSensor(SensorEntity, RestoreEntity):
    """Representation of a Virtual Power Sensor."""

    # Device Type	Approx. Wattage
    # LED Bulb	5 - 15 W
    # Incandescent Bulb	40 - 100 W
    # Smart Plug (idle)	1 - 2 W
    # Ceiling Fan	50 - 75 W
    # Laptop Charger	30 - 60 W
    # Desktop Computer	100 - 250 W
    # TV (LED/LCD)	50 - 150 W
    # Refrigerator	100 - 800 W
    # Air Conditioner	1,000 - 2,500 W
    # Heater	1,000 - 1,500 W
    # Router	5 - 15

    _attr_should_poll = False
    _attr_state_class = "measurement"

    def __init__(
        self,
        hass: HomeAssistant,
        entity_id: str,
        watts: float,
        profile_name: str | None = None,
    ):
        self.hass = hass
        self._entity_id = entity_id

        self._attr_device_class = SensorDeviceClass.POWER

        name = profile_name or entity_id.split(".")[-1]
        safe_name = name.lower().replace(" ", "_")
        self._attr_name = f"{name}_virtual_power"
        self._attr_unique_id = f"virtual_power_{safe_name}"
        self._state = 0.0  # Default power usage in watts
        self._watts = float(watts)

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._attr_name

    @property
    def unique_id(self):
        """Return a unique ID."""
        return self._attr_unique_id

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    @property
    def device_info(self):
        """Return information about the device."""
        return {
            "identifiers": {(DOMAIN, NAME)},
            "name": NAME,
            "manufacturer": NAME,
        }

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement."""
        return "W"

    @staticmethod
    def _is_active_state(state: str) -> bool:
        """Return True if a source entity state indicates active power usage."""
        return state.lower() not in {
            "off",
            "idle",
            "standby",
            "unknown",
            "unavailable",
            "not_home",
            "none",
        }

    @callback
    def update_virtual_power(self):
        """Update the power consumption based on the linked entity's state."""
        state = self.hass.states.get(self._entity_id)

        if state:
            self._state = self._watts if self._is_active_state(state.state) else 0.0
            _LOGGER.debug(
                "Entity: %s, State: %s, Power: %sW",
                self._entity_id,
                state.state,
                self._state,
            )
        else:
            self._state = 0.0

        self.async_write_ha_state()

    async def async_added_to_hass(self):
        """Register callbacks when the sensor is added to Home Assistant."""
        # Call super() first to restore previous state
        await super().async_added_to_hass()

        # Restore previous state if available
        if (last_state := await self.async_get_last_state()) is not None:
            try:
                self._state = float(last_state.state)
            except (TypeError, ValueError):
                self._state = 0.0

        # Register state change callback and store unsubscribe callback for cleanup
        self._unsubscribe = async_track_state_change_event(
            self.hass,
            [self._entity_id],
            lambda *_: self.update_virtual_power(),
        )
        self.update_virtual_power()

    async def async_will_remove_from_hass(self):
        """Clean up callbacks when the entity is removed."""
        if hasattr(self, '_unsubscribe') and self._unsubscribe:
            self._unsubscribe()
        await super().async_will_remove_from_hass()


class VirtualPowerSensorAlwaysOn(SensorEntity, RestoreEntity):
    """Representation of a Virtual Power Sensor."""

    _attr_should_poll = False
    _attr_state_class = "measurement"
    _attr_device_class = SensorDeviceClass.POWER

    def __init__(self, hass: HomeAssistant, entity_id: str, watts: float):
        self.hass = hass
        self._entity_id = entity_id
        safe_name = entity_id.lower().replace(" ", "_")
        self._attr_name = f"{entity_id}_virtual_power"
        self._attr_unique_id = f"virtual_power_{safe_name}"
        self._state = float(watts)
        self._watts = float(watts)

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._attr_name

    @property
    def unique_id(self):
        """Return a unique ID."""
        return self._attr_unique_id

    @property
    def device_info(self):
        """Return information about the device."""
        return {
            "identifiers": {(DOMAIN, NAME)},
            "name": NAME,
            "manufacturer": NAME,
        }

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement."""
        return "W"

    @callback
    def update_virtual_power(self):
        """Update the power consumption based on the linked entity's state."""
        self._state = self._watts
        self.async_write_ha_state()

    async def async_added_to_hass(self):
        """Register callbacks when the sensor is added to Home Assistant."""
        # Call super() first to restore previous state
        await super().async_added_to_hass()

        # Restore previous state if available
        if (last_state := await self.async_get_last_state()) is not None:
            try:
                self._state = float(last_state.state)
            except (TypeError, ValueError):
                self._state = self._watts

        self.update_virtual_power()

    async def async_will_remove_from_hass(self):
        """Clean up callbacks when the entity is removed."""
        await super().async_will_remove_from_hass()


class FakeDeviceVirtualPowerSensor(SensorEntity, RestoreEntity):
    """Representation of a fake device virtual power sensor."""

    def __init__(self, device_type: str, min_wattage: float, max_wattage: float):
        """Initialize the virtual power sensor."""
        self._device_type = device_type
        self._min_wattage = min_wattage
        self._max_wattage = max_wattage
        entity_id = f"sensor.{device_type.lower().replace(' ', '_')}_power"
        self._entity_id = entity_id
        self._attr_name = f"{entity_id}_virtual_power"
        self._attr_unique_id = f"virtual_power_{entity_id}"
        self._state = None

    @property
    def name(self):
        """Return the name of the sensor."""
        return f"{self._device_type} Power"

    @property
    def unique_id(self):
        """Return a unique ID."""
        return self._attr_unique_id

    @property
    def device_info(self):
        """Return information about the device."""
        return {
            "identifiers": {(DOMAIN, NAME)},
            "name": NAME,
            "manufacturer": NAME,
        }

    @property
    def state(self):
        """Return the current power usage."""
        return self._state

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement."""
        return "W"

    def update(self):
        """Simulate a power usage value."""
        self._state = round(uniform(self._min_wattage, self._max_wattage), 2)

    async def async_added_to_hass(self):
        """Restore previous state when entity is added."""
        await super().async_added_to_hass()

        if (last_state := await self.async_get_last_state()) is not None:
            self._state = last_state.state


class TotalEnergySensor(SensorEntity, RestoreEntity):
    """Representation of a total energy sensor."""

    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant):
        """Initialize the total energy sensor."""
        self.hass = hass
        self._state = 0.0
        entity_id = f"sensor.total_energy_usage"
        self._entity_id = entity_id
        self._attr_name = f"{entity_id}_energy"
        self._attr_unique_id = f"power_{entity_id}"
        self._unsubscribers: list[Any] = []

    @property
    def name(self):
        """Return the name of the sensor."""
        return "Total Energy Usage"

    @property
    def device_info(self):
        """Return information about the device."""
        return {
            "identifiers": {(DOMAIN, NAME)},
            "name": NAME,
            "manufacturer": NAME,
        }

    @property
    def unique_id(self):
        """Return a unique ID."""
        return self._attr_unique_id

    @property
    def state(self):
        """Return the total energy usage in kWh."""
        return self._state

    @property
    def device_class(self) -> str:
        """Return the device_class of the sensor."""
        return "energy"

    @property
    def state_class(self) -> str:
        """Return the state_class of the sensor."""
        return "total"

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement."""
        return "kWh"

    @staticmethod
    def _is_active_state(state: str) -> bool:
        """Return True if a source entity state indicates active power usage."""
        return state.lower() not in {
            "off",
            "idle",
            "standby",
            "unknown",
            "unavailable",
            "not_home",
            "none",
        }

    @callback
    def _recalculate_total_kw(self) -> None:
        """Recalculate total estimated home power usage from configured profiles."""
        profiles = self.hass.data.get(DOMAIN, {}).get("virtual_power_profiles", [])
        total_watts = 0.0

        for profile in profiles:
            try:
                wattage = float(profile.get("wattage", 0.0))
            except (TypeError, ValueError):
                continue

            if profile.get("always_on", False):
                total_watts += wattage
                continue

            source_entity_id = profile.get("entity_id")
            if not source_entity_id:
                continue

            source_state = self.hass.states.get(source_entity_id)
            if source_state and self._is_active_state(source_state.state):
                total_watts += wattage

        self._state = round(total_watts / 1000, 3)
        self.async_write_ha_state()

    async def async_added_to_hass(self):
        """Restore previous state when entity is added."""
        await super().async_added_to_hass()

        if (last_state := await self.async_get_last_state()) is not None:
            try:
                self._state = float(last_state.state)
            except (TypeError, ValueError):
                self._state = 0.0

        tracked_entities = {
            profile.get("entity_id")
            for profile in self.hass.data.get(DOMAIN, {}).get("virtual_power_profiles", [])
            if profile.get("entity_id") and not profile.get("always_on", False)
        }

        if tracked_entities:
            unsubscribe = async_track_state_change_event(
                self.hass,
                list(tracked_entities),
                lambda *_: self._recalculate_total_kw(),
            )
            self._unsubscribers.append(unsubscribe)

        self._recalculate_total_kw()

    async def async_will_remove_from_hass(self) -> None:
        """Clean up callbacks when the entity is removed."""
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()
        await super().async_will_remove_from_hass()