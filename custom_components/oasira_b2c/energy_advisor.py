import datetime
import logging
from homeassistant.helpers import entity_registry as er_module, area_registry, event
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = datetime.timedelta(hours=24)


async def async_setup_energy_advisor(hass):
    """Set up the daily Oasira Energy Advisor."""
    async def analyze_energy_usage(now):
        """Analyze last 24h of light and motion activity."""
        _LOGGER.debug("Running Oasira Energy Advisor analysis...")

        end_time = dt_util.utcnow()
        start_time = end_time - datetime.timedelta(hours=24)

        # Get entity registry for mapping entities to areas
        er = er_module.async_get(hass)
        ar = area_registry.async_get_registry(hass)

        areas = {}
        for entity_id, entry in er.entities.items():
            if not entry.area_id:
                continue

            if entry.area_id not in areas:
                areas[entry.area_id] = {"lights": [], "motion": []}

            if entity_id.startswith("light."):
                areas[entry.area_id]["lights"].append(entity_id)
            elif entity_id.startswith(("binary_sensor.", "sensor.")):
                if "motion" in entity_id or "presence" in entity_id:
                    areas[entry.area_id]["motion"].append(entity_id)

        from homeassistant.components.recorder import history
        suggestions = []

        for area_id, devices in areas.items():
            total_on_time = 0
            total_idle_time = 0

            # Calculate light-on durations
            for light in devices["lights"]:
                states = await history.get_significant_states(start_time, end_time, entity_ids=[light], hass=hass)
                for st in states.get(light, []):
                    if st.state == "on" and st.last_changed and st.last_updated:
                        total_on_time += (st.last_changed - st.last_updated).total_seconds()

            # Calculate idle motion durations
            for motion in devices["motion"]:
                states = await history.get_significant_states(start_time, end_time, entity_ids=[motion], hass=hass)
                for st in states.get(motion, []):
                    if st.state == "off" and st.last_changed and st.last_updated:
                        total_idle_time += (st.last_changed - st.last_updated).total_seconds()

            # Only suggest if lights were on > 30 mins and >90% of that time idle
            if total_on_time > 1800 and total_idle_time / (total_on_time + 1) > 0.9:
                area_name = ar.async_get_area(area_id).name if ar.async_get_area(area_id) else area_id
                hrs = round(total_on_time / 3600, 1)
                suggestions.append(
                    f"💡 **{area_name}** — Lights were on for about {hrs} hours yesterday with no motion detected."
                )

        if suggestions:
            msg = "### Oasira Energy Advisor\n\n" + "\n".join(suggestions)
            await hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": "Oasira Energy Advisor",
                    "message": msg,
                    "notification_id": "oasira_energy_advisor"
                },
            )
            _LOGGER.info("Oasira Energy Advisor: sent daily energy improvement report.")
        else:
            _LOGGER.info("Oasira Energy Advisor: no energy inefficiencies detected today.")

    # 🕘 Schedule daily run at 9:00
    event.async_track_time_change(hass, analyze_energy_usage, hour=9, minute=0, second=0)
    _LOGGER.info("Oasira Energy Advisor scheduled for daily energy analysis.")
