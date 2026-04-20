"""Automation Analysis function for Oasira AI Conversation."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from collections import defaultdict, Counter
import re
import httpx

import voluptuous as vol

from homeassistant.components import recorder
from homeassistant.const import (
    STATE_ON,
    STATE_OFF,
    STATE_HOME,
    STATE_NOT_HOME,
    ATTR_FRIENDLY_NAME,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .base import Function
from ..ai_exceptions import EntityNotExposed, EntityNotFound, InvalidFunction

_LOGGER = logging.getLogger(__name__)

# Pattern detection constants
MIN_PATTERN_OCCURRENCES = 3
MIN_PATTERN_CONFIDENCE = 0.7
DEFAULT_ANALYSIS_DAYS = 7
DEFAULT_TIME_WINDOW_MINUTES = 30

# Entity type mappings for pattern analysis
ENTITY_TYPE_LIGHTS = ["light", "switch"]
ENTITY_TYPE_SENSORS = ["binary_sensor", "sensor"]
ENTITY_TYPE_CLIMATE = ["climate", "cover"]
ENTITY_TYPE_PERSON = ["person", "device_tracker"]

# Automation template types
AUTOMATION_TYPES = {
    "light_schedule": "Light Schedule Automation",
    "motion_light": "Motion-Activated Light",
    "climate_schedule": "Climate Schedule",
    "presence_automation": "Presence-Based Automation",
    "sensor_trigger": "Sensor-Based Trigger",
    "energy_saving": "Energy Saving Automation",
}


class AutomationAnalysisFunction(Function):
    """Function to analyze home usage patterns and recommend automations."""

    def __init__(self) -> None:
        """Initialize the automation analysis function."""
        super().__init__(
            vol.Schema({
                vol.Required("type"): str,
                vol.Optional("time_range_days", default=DEFAULT_ANALYSIS_DAYS): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=90)
                ),
                vol.Optional("entity_types", default=[]): [str],
                vol.Optional("include_entities", default=[]): [str],
                vol.Optional("exclude_entities", default=[]): [str],
                vol.Optional("pattern_types", default=[]): [str],
                vol.Optional("min_confidence", default=MIN_PATTERN_CONFIDENCE): vol.All(
                    vol.Coerce(float), vol.Range(min=0.0, max=1.0)
                ),
                vol.Optional("time_window_minutes", default=DEFAULT_TIME_WINDOW_MINUTES): vol.All(
                    vol.Coerce(int), vol.Range(min=5, max=1440)
                ),
                vol.Optional("create_automations", default=False): bool,
            })
        )

    async def execute(
        self,
        hass: HomeAssistant,
        function_config: dict[str, Any],
        arguments: dict[str, Any],
        llm_context: Any = None,
        exposed_entities: list[dict[str, Any]] = None,
        client: Any = None,
    ) -> dict[str, Any]:
        """Execute the automation analysis function."""
        # AI is REQUIRED for this function - raise error if client not available
        if client is None:
            _LOGGER.error("Ollama AI client is required for automation analysis")
            return {
                "status": "error",
                "message": "AI client is required but not available. Please ensure Ollama is configured.",
                "recommendations": [],
                "patterns": []
            }
        
        try:
            # Validate configuration
            config = self.validate_schema(function_config)
            
            # Get analysis parameters
            time_range_days = config.get("time_range_days", DEFAULT_ANALYSIS_DAYS)
            entity_types = config.get("entity_types", [])
            include_entities = config.get("include_entities", [])
            exclude_entities = config.get("exclude_entities", [])
            pattern_types = config.get("pattern_types", [])
            min_confidence = config.get("min_confidence", MIN_PATTERN_CONFIDENCE)
            time_window_minutes = config.get("time_window_minutes", DEFAULT_TIME_WINDOW_MINUTES)
            create_automations = config.get("create_automations", False)

            _LOGGER.info("Starting automation analysis: days=%d, entity_types=%s, min_confidence=%.2f, time_window=%d, create_automations=%s",
                        time_range_days, entity_types, min_confidence, time_window_minutes, create_automations)

            # Get exposed entities if not provided
            if exposed_entities is None:
                exposed_entities = self._get_exposed_entities(hass)

            # Filter entities for analysis
            entities_to_analyze = self._filter_entities(
                hass, exposed_entities, entity_types, include_entities, exclude_entities
            )

            if not entities_to_analyze:
                _LOGGER.warning("No entities found for analysis after filtering")
                return {
                    "status": "no_entities",
                    "message": "No entities found for analysis",
                    "recommendations": [],
                    "patterns": []
                }

            _LOGGER.info("Analyzing %d entities for patterns", len(entities_to_analyze))

            # Analyze patterns
            _LOGGER.info("Starting pattern analysis for %d entities over %d days", len(entities_to_analyze), time_range_days)
            patterns = await self._analyze_patterns(
                hass, entities_to_analyze, time_range_days, time_window_minutes
            )

            _LOGGER.info("Found %d raw patterns before filtering", len(patterns))
            
            # Filter patterns by type and confidence
            if pattern_types:
                patterns = [p for p in patterns if p.get("pattern_type") in pattern_types]
            
            patterns_before_confidence = len(patterns)
            patterns = [p for p in patterns if p.get("confidence", 0) >= min_confidence]
            
            _LOGGER.info("After confidence filtering (%.2f): %d/%d patterns remain", 
                        min_confidence, len(patterns), patterns_before_confidence)
            
            # Debug: Log what entities were analyzed
            if not patterns:
                _LOGGER.warning("No patterns found - debugging entity analysis:")
                for entity in entities_to_analyze[:5]:  # Log first 5 entities
                    entity_id = entity.get("entity_id", "")
                    entity_type = entity_id.split(".")[0] if "." in entity_id else ""
                    _LOGGER.warning("  Analyzed entity: %s (type: %s)", entity_id, entity_type)
                
                # Additional debugging - check if we're getting any history at all
                _LOGGER.warning("Checking history for first few entities:")
                for entity in entities_to_analyze[:3]:
                    entity_id = entity.get("entity_id", "")
                    try:
                        # Use the same time range as the main analysis
                        debug_end_time = dt_util.utcnow()
                        debug_start_time = debug_end_time - timedelta(days=time_range_days)
                        
                        entity_history = await self._get_entity_history(
                            hass, entity_id, debug_start_time, debug_end_time
                        )
                        _LOGGER.warning("  Entity %s: %d history entries", entity_id, len(entity_history))
                        if entity_history:
                            # Log first few entries
                            for i, state in enumerate(entity_history[:3]):
                                _LOGGER.warning("    Entry %d: %s -> %s at %s", 
                                              i, state.entity_id, state.state, state.last_changed)
                    except Exception as e:
                        _LOGGER.warning("  Entity %s: Error getting history - %s", entity_id, e)

            # Debug: Log pattern detection results before AI enhancement
            _LOGGER.info("=== PATTERN DETECTION RESULTS ===")
            _LOGGER.info("Total patterns detected: %d", len(patterns))
            _LOGGER.info("Total entities analyzed: %d", len(entities_to_analyze))
            _LOGGER.info("Time range used: %d days", time_range_days)
            _LOGGER.info("Min confidence threshold: %.2f", min_confidence)
            
            if not patterns:
                _LOGGER.warning("NO PATTERNS DETECTED - But will still send to AI for entity-based suggestions!")
                _LOGGER.warning("Pattern detection pipeline is failing - need to debug entity history retrieval")
            
            # Generate automation recommendations using AI (MANDATORY)
            # Always call AI even if no patterns detected - AI can suggest based on entity types
            _LOGGER.info("Enhancing recommendations with Ollama AI")
            _LOGGER.info("Client object: %s, Type: %s", client, type(client))
            _LOGGER.info("Patterns to enhance: %d", len(patterns))
            _LOGGER.info("Entities to analyze: %d", len(entities_to_analyze))
            
            recommendations = await self._enhance_with_ai(
                client, patterns, entities_to_analyze, time_range_days
            )
            
            _LOGGER.info("AI generated %d recommendations", len(recommendations))

            # Handle create_automations flag
            if create_automations and recommendations:
                _LOGGER.info("Creating automation YAML for %d recommendations", len(recommendations))
                automation_yaml = self._generate_automation_yaml(recommendations)
                return {
                    "status": "success",
                    "automations_created": len(recommendations),
                    "automation_yaml": automation_yaml,
                    "summary": self._generate_summary(patterns, recommendations, entities_to_analyze),
                    "patterns": patterns,
                    "recommendations": recommendations,
                    "analysis_parameters": {
                        "time_range_days": time_range_days,
                        "entity_count": len(entities_to_analyze),
                        "min_confidence": min_confidence,
                        "time_window_minutes": time_window_minutes,
                        "ai_enhanced": True,
                    }
                }

            # Generate summary report
            summary = self._generate_summary(patterns, recommendations, entities_to_analyze)

            return {
                "status": "success",
                "summary": summary,
                "patterns": patterns,
                "recommendations": recommendations,
                "analysis_parameters": {
                    "time_range_days": time_range_days,
                    "entity_count": len(entities_to_analyze),
                    "min_confidence": min_confidence,
                    "time_window_minutes": time_window_minutes,
                    "ai_enhanced": True,
                }
            }

        except Exception as e:
            _LOGGER.error("Error in automation analysis: %s", e, exc_info=True)
            return {
                "status": "error",
                "message": f"Analysis failed: {str(e)}",
                "recommendations": [],
                "patterns": []
            }

    def _get_exposed_entities(self, hass: HomeAssistant) -> list[dict[str, Any]]:
        """Get all exposed entities from Home Assistant."""
        from ..ai_helpers import get_exposed_entities
        return get_exposed_entities(hass)

    def _filter_entities(
        self,
        hass: HomeAssistant,
        exposed_entities: list[dict[str, Any]],
        entity_types: list[str],
        include_entities: list[str],
        exclude_entities: list[str],
    ) -> list[dict[str, Any]]:
        """Filter entities based on type and inclusion/exclusion lists."""
        filtered_entities = []

        for entity in exposed_entities:
            entity_id = entity.get("entity_id", "")
            entity_type = entity_id.split(".")[0] if "." in entity_id else ""

            # Skip if entity is in exclude list
            if entity_id in exclude_entities:
                continue

            # Include if in include list or if entity types match
            should_include = False
            if include_entities and entity_id in include_entities:
                should_include = True
            elif entity_types and entity_type in entity_types:
                should_include = True
            elif not entity_types and not include_entities:
                # Include all if no filters specified
                should_include = True

            if should_include:
                filtered_entities.append(entity)

        return filtered_entities

    async def _analyze_patterns(
        self,
        hass: HomeAssistant,
        entities: list[dict[str, Any]],
        time_range_days: int,
        time_window_minutes: int,
    ) -> list[dict[str, Any]]:
        """Analyze usage patterns from entity history."""
        patterns = []
        
        end_time = dt_util.utcnow()
        start_time = end_time - timedelta(days=time_range_days)

        _LOGGER.info("=== PATTERN DETECTION PIPELINE ===")
        _LOGGER.info("Analyzing patterns for %d entities from %s to %s", 
                    len(entities), start_time.strftime("%Y-%m-%d"), end_time.strftime("%Y-%m-%d"))

        for entity in entities:
            entity_id = entity.get("entity_id", "")
            entity_type = entity_id.split(".")[0] if "." in entity_id else ""
            entity_friendly_name = entity.get("name", entity.get("attributes", {}).get("friendly_name", entity_id))
            
            try:
                # Get entity history
                _LOGGER.debug("Getting history for entity: %s (type: %s)", entity_id, entity_type)
                _LOGGER.debug("Time range: %s to %s", start_time.strftime("%Y-%m-%d %H:%M"), end_time.strftime("%Y-%m-%d %H:%M"))
                
                entity_history = await self._get_entity_history(
                    hass, entity_id, start_time, end_time
                )

                if not entity_history:
                    _LOGGER.debug("No history found for entity: %s", entity_id)
                    continue

                _LOGGER.debug("Found %d history entries for %s", len(entity_history), entity_id)
                
                # Debug: Log all history entries for this entity
                _LOGGER.debug("ALL history entries for %s:", entity_id)
                for i, state in enumerate(entity_history):
                    _LOGGER.debug("  %d: %s -> %s at %s", i, state.entity_id, state.state, state.last_changed)
                
                # Debug: Check what states we're getting
                states_found = set(state.state for state in entity_history)
                _LOGGER.debug("States found for %s: %s", entity_id, states_found)

                # Analyze patterns based on entity type
                if entity_type in ENTITY_TYPE_LIGHTS:
                    _LOGGER.debug("Analyzing light patterns for %s", entity_id)
                    light_patterns = self._analyze_light_patterns(
                        entity_history, time_window_minutes
                    )
                    _LOGGER.debug("Found %d light patterns for %s", len(light_patterns), entity_id)
                    # Add entity info to each pattern
                    for pattern in light_patterns:
                        pattern["source_entity"] = entity_id
                        pattern["source_entity_name"] = entity_friendly_name
                    patterns.extend(light_patterns)
                
                elif entity_type in ENTITY_TYPE_SENSORS:
                    _LOGGER.debug("Analyzing sensor patterns for %s", entity_id)
                    sensor_patterns = self._analyze_sensor_patterns(
                        entity_history, time_window_minutes
                    )
                    _LOGGER.debug("Found %d sensor patterns for %s", len(sensor_patterns), entity_id)
                    for pattern in sensor_patterns:
                        pattern["source_entity"] = entity_id
                        pattern["source_entity_name"] = entity_friendly_name
                    patterns.extend(sensor_patterns)
                
                elif entity_type in ENTITY_TYPE_PERSON:
                    _LOGGER.debug("Analyzing presence patterns for %s", entity_id)
                    presence_patterns = self._analyze_presence_patterns(
                        entity_history, time_window_minutes
                    )
                    _LOGGER.debug("Found %d presence patterns for %s", len(presence_patterns), entity_id)
                    for pattern in presence_patterns:
                        pattern["source_entity"] = entity_id
                        pattern["source_entity_name"] = entity_friendly_name
                    patterns.extend(presence_patterns)
                else:
                    _LOGGER.debug("Entity type %s not supported for pattern analysis: %s", entity_type, entity_id)

            except Exception as e:
                _LOGGER.warning("Failed to analyze patterns for %s: %s", entity_id, e)

        _LOGGER.info("Total patterns found: %d", len(patterns))
        _LOGGER.info("=== PATTERN DETECTION PIPELINE COMPLETE ===")
        return patterns

    async def _get_entity_history(
        self,
        hass: HomeAssistant,
        entity_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[State]:
        """Get entity state history from recorder."""
        try:
            # Use the same approach as native.py get_history function
            from homeassistant.components.recorder import history as recorder_history
            
            _LOGGER.debug("Querying recorder for %s from %s to %s", entity_id, start_time, end_time)
            
            with recorder.util.session_scope(hass=hass, read_only=True) as session:
                result = await recorder.get_instance(hass).async_add_executor_job(
                    recorder_history.get_significant_states_with_session,
                    hass,
                    session,
                    start_time,
                    end_time,
                    [entity_id],
                    None,  # filters
                    True,  # include_start_time_state
                    False,  # significant_changes_only
                    False,  # minimal_response
                    False,  # no_attributes
                )
                
            _LOGGER.debug("Recorder query completed for %s", entity_id)
            
            if result and entity_id in result:
                history_entries = result[entity_id]
                _LOGGER.debug("Recorder returned %d history entries for %s", len(history_entries), entity_id)
                return history_entries
            else:
                _LOGGER.debug("Recorder returned no history for %s", entity_id)
                return []

        except Exception as e:
            _LOGGER.debug("Failed to get history for %s: %s", entity_id, e)
            return []

    def _analyze_light_patterns(
        self, 
        entity_history: list[State], 
        time_window_minutes: int
    ) -> list[dict[str, Any]]:
        """Analyze lighting usage patterns."""
        patterns = []
        
        if not entity_history:
            _LOGGER.debug("No entity history provided for light pattern analysis")
            return patterns

        # Group state changes by day and time
        daily_patterns = defaultdict(list)
        
        # Include both light and switch states
        on_off_states = [STATE_ON, STATE_OFF]
        state_changes = 0
        
        for state in entity_history:
            if state.state in on_off_states:
                state_changes += 1
                # Extract time components
                time_key = state.last_changed.strftime("%H:%M")
                day_of_week = state.last_changed.weekday()  # 0=Monday, 6=Sunday
                date_key = state.last_changed.strftime("%Y-%m-%d")
                
                daily_patterns[date_key].append({
                    "time": time_key,
                    "state": state.state,
                    "day_of_week": day_of_week,
                    "timestamp": state.last_changed
                })

        _LOGGER.debug("Found %d ON/OFF state changes across %d days", state_changes, len(daily_patterns))
        
        # Debug: Log daily patterns
        for date, changes in list(daily_patterns.items())[:3]:  # Log first 3 days
            _LOGGER.debug("Day %s: %d state changes", date, len(changes))
            for change in changes[:5]:  # Log first 5 changes per day
                _LOGGER.debug("  %s -> %s at %s", change["state"], change["time"], change["timestamp"])

        # Analyze daily routines
        weekday_patterns = []
        weekend_patterns = []
        
        for date, changes in daily_patterns.items():
            # Sort by time
            changes.sort(key=lambda x: x["timestamp"])
            
            # Determine if weekday or weekend
            is_weekend = changes[0]["day_of_week"] >= 5
            
            # Find ON/OFF cycles
            cycles = self._extract_light_cycles(changes)
            
            _LOGGER.debug("Day %s (%s): %d cycles found", date, "weekend" if is_weekend else "weekday", len(cycles))
            
            if is_weekend:
                weekend_patterns.extend(cycles)
            else:
                weekday_patterns.extend(cycles)

        _LOGGER.debug("Total weekday cycles: %d, weekend cycles: %d", len(weekday_patterns), len(weekend_patterns))

        # Generate patterns for weekdays
        if weekday_patterns:
            _LOGGER.debug("Generating weekday pattern from %d cycles", len(weekday_patterns))
            weekday_pattern = self._generate_light_pattern(
                weekday_patterns, "weekday", time_window_minutes
            )
            if weekday_pattern:
                _LOGGER.debug("Generated weekday pattern: %s", weekday_pattern.get("description", ""))
                patterns.append(weekday_pattern)
            else:
                _LOGGER.debug("No weekday pattern generated (likely low confidence)")

        # Generate patterns for weekends
        if weekend_patterns:
            _LOGGER.debug("Generating weekend pattern from %d cycles", len(weekend_patterns))
            weekend_pattern = self._generate_light_pattern(
                weekend_patterns, "weekend", time_window_minutes
            )
            if weekend_pattern:
                _LOGGER.debug("Generated weekend pattern: %s", weekend_pattern.get("description", ""))
                patterns.append(weekend_pattern)
            else:
                _LOGGER.debug("No weekend pattern generated (likely low confidence)")

        _LOGGER.debug("Total light patterns found: %d", len(patterns))
        return patterns

    def _extract_light_cycles(self, changes: list[dict]) -> list[dict]:
        """Extract ON/OFF cycles from state changes."""
        cycles = []
        current_cycle = None
        
        for change in changes:
            if change["state"] == STATE_ON and (current_cycle is None or current_cycle["state"] == STATE_OFF):
                if current_cycle:
                    cycles.append(current_cycle)
                
                current_cycle = {
                    "start_time": change["time"],
                    "start_timestamp": change["timestamp"],
                    "state": STATE_ON,
                    "end_time": None,
                    "end_timestamp": None
                }
            elif change["state"] == STATE_OFF and current_cycle and current_cycle["state"] == STATE_ON:
                current_cycle["end_time"] = change["time"]
                current_cycle["end_timestamp"] = change["timestamp"]
                cycles.append(current_cycle)
                current_cycle = None

        # Handle case where light is still ON at end of period
        if current_cycle and current_cycle["state"] == STATE_ON:
            cycles.append(current_cycle)

        return cycles

    def _generate_light_pattern(
        self, 
        cycles: list[dict], 
        day_type: str, 
        time_window_minutes: int,
        entity_id: str = None
    ) -> Optional[dict[str, Any]]:
        """Generate a light usage pattern from cycles."""
        if not cycles:
            return None

        # Extract start and end times
        start_times = [cycle["start_time"] for cycle in cycles if cycle.get("start_time")]
        end_times = [cycle["end_time"] for cycle in cycles if cycle.get("end_time")]

        if not start_times or not end_times:
            return None

        # Calculate most common times
        start_time_pattern = self._find_time_pattern(start_times, time_window_minutes)
        end_time_pattern = self._find_time_pattern(end_times, time_window_minutes)

        if not start_time_pattern or not end_time_pattern:
            return None

        # Calculate confidence based on pattern consistency
        confidence = self._calculate_pattern_confidence(cycles, start_time_pattern, end_time_pattern)

        if confidence < MIN_PATTERN_CONFIDENCE:
            return None

        return {
            "pattern_type": "light_schedule",
            "entity_type": "light",
            "description": f"Light turns on around {start_time_pattern} and off around {end_time_pattern} on {day_type}s",
            "start_time": start_time_pattern,
            "end_time": end_time_pattern,
            "day_type": day_type,
            "confidence": confidence,
            "occurrences": len(cycles),
            "automation_type": "light_schedule",
        }

    def _find_time_pattern(self, times: list[str], time_window_minutes: int) -> Optional[str]:
        """Find the most common time pattern within a time window."""
        if not times:
            return None

        # Convert times to minutes since midnight for easier calculation
        time_minutes = []
        for time_str in times:
            try:
                hour, minute = map(int, time_str.split(":"))
                total_minutes = hour * 60 + minute
                time_minutes.append(total_minutes)
            except ValueError:
                continue

        if not time_minutes:
            return None

        # Find clusters of times within the window
        clusters = []
        for time in time_minutes:
            cluster = [t for t in time_minutes if abs(t - time) <= time_window_minutes]
            if len(cluster) >= MIN_PATTERN_OCCURRENCES:
                clusters.append(cluster)

        if not clusters:
            return None

        # Find the largest cluster
        largest_cluster = max(clusters, key=len)
        
        # Calculate average time of the cluster
        avg_minutes = sum(largest_cluster) / len(largest_cluster)
        
        # Convert back to time format
        hour = int(avg_minutes // 60)
        minute = int(avg_minutes % 60)
        
        return f"{hour:02d}:{minute:02d}"

    def _calculate_pattern_confidence(self, cycles: list[dict], start_pattern: str, end_pattern: str) -> float:
        """Calculate confidence score for a pattern."""
        if not cycles:
            return 0.0

        start_hour, start_minute = map(int, start_pattern.split(":"))
        end_hour, end_minute = map(int, end_pattern.split(":"))
        
        start_minutes = start_hour * 60 + start_minute
        end_minutes = end_hour * 60 + end_minute

        # Count cycles that match the pattern (within 30 minutes)
        matching_cycles = 0
        for cycle in cycles:
            if not cycle.get("start_time") or not cycle.get("end_time"):
                continue
                
            cycle_start = self._time_to_minutes(cycle["start_time"])
            cycle_end = self._time_to_minutes(cycle["end_time"])
            
            if (abs(cycle_start - start_minutes) <= 30 and 
                abs(cycle_end - end_minutes) <= 30):
                matching_cycles += 1

        return matching_cycles / len(cycles)

    def _time_to_minutes(self, time_str: str) -> int:
        """Convert time string to minutes since midnight."""
        try:
            hour, minute = map(int, time_str.split(":"))
            return hour * 60 + minute
        except ValueError:
            return 0

    def _analyze_sensor_patterns(
        self, 
        entity_history: list[State], 
        time_window_minutes: int
    ) -> list[dict[str, Any]]:
        """Analyze sensor usage patterns."""
        patterns = []
        
        if not entity_history:
            _LOGGER.debug("No entity history provided for sensor pattern analysis")
            return patterns

        # Group by day
        daily_patterns = defaultdict(list)
        
        for state in entity_history:
            date_key = state.last_changed.strftime("%Y-%m-%d")
            daily_patterns[date_key].append({
                "time": state.last_changed.strftime("%H:%M"),
                "state": state.state,
                "timestamp": state.last_changed
            })

        _LOGGER.debug("Found %d days of sensor data", len(daily_patterns))
        
        # Debug: Log daily patterns
        for date, changes in list(daily_patterns.items())[:3]:  # Log first 3 days
            _LOGGER.debug("Day %s: %d state changes", date, len(changes))
            for change in changes[:5]:  # Log first 5 changes per day
                _LOGGER.debug("  %s -> %s at %s", change["state"], change["time"], change["timestamp"])

        # Analyze patterns for each day
        for date, changes in daily_patterns.items():
            changes.sort(key=lambda x: x["timestamp"])
            
            # Look for recurring patterns
            active_periods = self._find_active_periods(changes)
            
            _LOGGER.debug("Day %s: %d active periods found", date, len(active_periods))
            
            for period in active_periods:
                pattern = {
                    "pattern_type": "sensor_activity",
                    "description": f"Sensor active from {period['start']} to {period['end']}",
                    "start_time": period["start"],
                    "end_time": period["end"],
                    "confidence": 0.8,  # Default confidence for sensor patterns
                    "occurrences": 1,
                    "automation_type": "sensor_trigger"
                }
                patterns.append(pattern)

        _LOGGER.debug("Total sensor patterns found: %d", len(patterns))
        return patterns

    def _find_active_periods(self, changes: list[dict]) -> list[dict]:
        """Find periods when sensor was active."""
        periods = []
        current_period = None
        
        for change in changes:
            if change["state"] == STATE_ON:
                if current_period is None:
                    current_period = {
                        "start": change["time"],
                        "end": change["time"]
                    }
                else:
                    current_period["end"] = change["time"]
            elif change["state"] == STATE_OFF and current_period:
                periods.append(current_period)
                current_period = None

        if current_period:
            periods.append(current_period)

        return periods

    def _analyze_presence_patterns(
        self, 
        entity_history: list[State], 
        time_window_minutes: int
    ) -> list[dict[str, Any]]:
        """Analyze presence/movement patterns."""
        patterns = []
        
        if not entity_history:
            _LOGGER.debug("No entity history provided for presence pattern analysis")
            return patterns

        # Group by day
        daily_patterns = defaultdict(list)
        
        for state in entity_history:
            date_key = state.last_changed.strftime("%Y-%m-%d")
            daily_patterns[date_key].append({
                "time": state.last_changed.strftime("%H:%M"),
                "state": state.state,
                "timestamp": state.last_changed
            })

        _LOGGER.debug("Found %d days of presence data", len(daily_patterns))
        
        # Debug: Log daily patterns
        for date, changes in list(daily_patterns.items())[:3]:  # Log first 3 days
            _LOGGER.debug("Day %s: %d state changes", date, len(changes))
            for change in changes[:5]:  # Log first 5 changes per day
                _LOGGER.debug("  %s -> %s at %s", change["state"], change["time"], change["timestamp"])

        # Analyze presence patterns
        for date, changes in daily_patterns.items():
            changes.sort(key=lambda x: x["timestamp"])
            
            # Look for home/away patterns
            presence_periods = self._find_presence_periods(changes)
            
            _LOGGER.debug("Day %s: %d presence periods found", date, len(presence_periods))
            
            for period in presence_periods:
                if period["state"] == STATE_HOME:
                    pattern = {
                        "pattern_type": "presence_home",
                        "description": f"Person home from {period['start']} to {period['end']}",
                        "start_time": period["start"],
                        "end_time": period["end"],
                        "confidence": 0.8,
                        "occurrences": 1,
                        "automation_type": "presence_automation"
                    }
                    patterns.append(pattern)

        _LOGGER.debug("Total presence patterns found: %d", len(patterns))
        return patterns

    def _find_presence_periods(self, changes: list[dict]) -> list[dict]:
        """Find periods of presence (home/away)."""
        periods = []
        current_period = None
        
        for change in changes:
            if change["state"] in [STATE_HOME, STATE_NOT_HOME]:
                if current_period is None or current_period["state"] != change["state"]:
                    if current_period:
                        periods.append(current_period)
                    
                    current_period = {
                        "start": change["time"],
                        "end": change["time"],
                        "state": change["state"]
                    }
                else:
                    current_period["end"] = change["time"]

        if current_period:
            periods.append(current_period)

        return periods

    async def _enhance_with_ai(
        self,
        client: Any,
        patterns: list[dict],
        entities: list[dict[str, Any]],
        time_range_days: int,
    ) -> list[dict[str, Any]]:
        """Enhance automation recommendations using AI analysis - MANDATORY."""
        
        # Check if we have patterns to process
        if not patterns:
            _LOGGER.info("No patterns found for AI enhancement - will analyze entities for suggestions")
        
        # Debug: Log entity information for troubleshooting
        _LOGGER.info("=== OLLAMA AI DEBUGGING ===")
        _LOGGER.info("Total entities available: %d", len(entities))
        _LOGGER.info("Total patterns found: %d", len(patterns))
        _LOGGER.info("Time range days: %d", time_range_days)
        
        # Debug: Log entity details
        _LOGGER.info("=== ENTITY DETAILS ===")
        for i, entity in enumerate(entities[:10]):  # Log first 10 entities
            entity_id = entity.get("entity_id", "")
            entity_type = entity_id.split(".")[0] if "." in entity_id else ""
            entity_name = entity.get("name", entity.get("attributes", {}).get("friendly_name", entity_id))
            _LOGGER.info("Entity %d: %s (type: %s, name: %s)", i+1, entity_id, entity_type, entity_name)
        
        # Debug: Log pattern details
        _LOGGER.info("=== PATTERN DETAILS ===")
        for i, pattern in enumerate(patterns):
            source = pattern.get("source_entity", "unknown")
            desc = pattern.get("description", "")
            conf = pattern.get("confidence", 0)
            ptype = pattern.get("pattern_type", "")
            start = pattern.get("start_time", "")
            end = pattern.get("end_time", "")
            day = pattern.get("day_type", "daily")
            _LOGGER.info("Pattern %d: Entity=%s, Type=%s, Times=%s-%s (%s), Confidence=%.0%%, Description=%s", 
                        i+1, source, ptype, start, end, day, conf*100, desc)
        
        # Prepare context with ACTUAL entity IDs from patterns OR entities if no patterns
        patterns_text = []
        entities_text = []
        
        # If we have patterns, use them
        if patterns:
            for p in patterns:
                source = p.get("source_entity", "unknown")
                desc = p.get("description", "")
                conf = p.get("confidence", 0)
                ptype = p.get("pattern_type", "")
                start = p.get("start_time", "")
                end = p.get("end_time", "")
                day = p.get("day_type", "daily")
                patterns_text.append(f"- Entity: {source} | Type: {ptype} | Times: {start}-{end} ({day}) | {desc} | Confidence: {conf:.0%}")
        else:
            # If no patterns, provide entity information for AI to suggest automations
            for entity in entities:
                entity_id = entity.get("entity_id", "")
                entity_type = entity_id.split(".")[0] if "." in entity_id else ""
                entity_name = entity.get("name", entity.get("attributes", {}).get("friendly_name", entity_id))
                entities_text.append(f"- Entity: {entity_id} | Type: {entity_type} | Name: {entity_name}")

        patterns_context = "\n".join(patterns_text) if patterns_text else "No usage patterns detected in the analysis period."
        entities_context = "\n".join(entities_text) if entities_text else "No entities available for analysis."
        
        # Create AI prompt with actual entity data - ENHANCED to request complete HA automation structure
        if patterns:
            prompt = f"""Based on these detected Home Assistant patterns, generate automation recommendations with COMPLETE Home Assistant trigger and action configurations.

Return ONLY valid JSON (no markdown) in this exact format:

{{"enhanced_recommendations": [
  {{
    "type": "light_schedule",
    "title": "Morning Light Routine",
    "description": "Turn on living room lights at 7am on weekdays",
    "confidence": 0.85,
    "entities": ["light.living_room", "light.kitchen"],
    "start_time": "07:00",
    "end_time": "23:00",
    "day_type": "weekday",
    "benefits": ["Energy savings", "Convenience"],
    "complexity": "Easy",
    "trigger": {{
      "platform": "time",
      "at": "07:00:00"
    }},
    "action": {{
      "service": "light.turn_on",
      "target": {{
        "entity_id": ["light.living_room", "light.kitchen"]
      }}
    }},
    "condition": {{
      "platform": "time",
      "weekdays": ["mon", "tue", "wed", "thu", "fri"]
    }}
  }}
], "additional_insights": "Analysis summary"}}

DETECTED PATTERNS:
{patterns_context}

IMPORTANT: 
- Include a complete "trigger" object with proper Home Assistant trigger configuration
- Include a complete "action" object with proper Home Assistant service call
- Include an optional "condition" object if applicable
- Use entity IDs from the patterns above for entities list
- Return ONLY the JSON object - no markdown code blocks or other text"""
        else:
            prompt = f"""Based on these Home Assistant entities, analyze the entity types and suggest potential automation recommendations with COMPLETE Home Assistant trigger and action configurations.

Return ONLY valid JSON (no markdown) in this exact format:

{{"enhanced_recommendations": [
  {{
    "type": "light_schedule",
    "title": "Morning Light Routine",
    "description": "Turn on living room lights at 7am on weekdays",
    "confidence": 0.85,
    "entities": ["light.living_room", "light.kitchen"],
    "start_time": "07:00",
    "end_time": "23:00",
    "day_type": "weekday",
    "benefits": ["Energy savings", "Convenience"],
    "complexity": "Easy",
    "trigger": {{
      "platform": "time",
      "at": "07:00:00"
    }},
    "action": {{
      "service": "light.turn_on",
      "target": {{
        "entity_id": ["light.living_room", "light.kitchen"]
      }}
    }},
    "condition": {{
      "platform": "time",
      "weekdays": ["mon", "tue", "wed", "thu", "fri"]
    }}
  }},
  {{
    "type": "motion_light",
    "title": "Motion Activated Hallway Light",
    "description": "Turn on hallway light when motion detected",
    "confidence": 0.9,
    "entities": ["binary_sensor.hallway_motion", "light.hallway"],
    "benefits": ["Safety", "Convenience"],
    "complexity": "Easy",
    "trigger": {{
      "platform": "state",
      "entity_id": "binary_sensor.hallway_motion",
      "to": "on"
    }},
    "action": {{
      "service": "light.turn_on",
      "target": {{
        "entity_id": "light.hallway"
      }}
    }}
  }},
  {{
    "type": "presence_automation",
    "title": "Welcome Home",
    "description": "Turn on lights when arriving home",
    "confidence": 0.8,
    "entities": ["person.family_member", "light.living_room", "switch.plug_1"],
    "benefits": ["Convenience", "Security"],
    "complexity": "Easy",
    "trigger": {{
      "platform": "state",
      "entity_id": "person.family_member",
      "to": "home"
    }},
    "action": {{
      "service": "homeassistant.turn_on",
      "target": {{
        "entity_id": ["light.living_room", "switch.plug_1"]
      }}
    }}
  }},
  {{
    "type": "energy_saving",
    "title": "Night Power Off",
    "description": "Turn off unused devices at night",
    "confidence": 0.75,
    "entities": ["switch.ac_unit", "switch.desk_lamp"],
    "benefits": ["Energy savings"],
    "complexity": "Easy",
    "trigger": {{
      "platform": "time",
      "at": "23:00:00"
    }},
    "action": {{
      "service": "switch.turn_off",
      "target": {{
        "entity_id": ["switch.ac_unit", "switch.desk_lamp"]
      }}
    }}
  }}
], "additional_insights": "Analysis summary"}}

AVAILABLE ENTITIES:
{entities_context}

ANALYSIS CONTEXT:
- Time range analyzed: {time_range_days} days
- No usage patterns were detected - suggest common smart home automations based on entity types

IMPORTANT: 
- Include a complete "trigger" object with proper Home Assistant trigger configuration
- Include a complete "action" object with proper Home Assistant service call  
- Include an optional "condition" object if applicable
- Use entity IDs from the available entities above
- For motion_light type, include binary_sensor and light entities
- For presence_automation, include person/device_tracker entities
- For light_schedule, include light entities with times
- Return ONLY the JSON object - no markdown code blocks or other text"""

        _LOGGER.info("=== OLLAMA API CALL ===")
        _LOGGER.info("Prompt length: %d characters", len(prompt))
        _LOGGER.info("Prompt preview (first 300 chars): %s", prompt[:300])
        _LOGGER.info("Calling Ollama AI for automation recommendations")

        # Validate that we have a proper client
        if client is None:
            _LOGGER.error("Ollama client is None - cannot proceed with AI enhancement")
            raise Exception("AI client is required but not available. Please ensure Ollama is configured.")
        
        # Check if client has the required methods
        if not hasattr(client, 'list_models') or not callable(getattr(client, 'list_models', None)):
            _LOGGER.error("Ollama client does not have list_models method. Client type: %s, Methods: %s", 
                         type(client), dir(client))
            raise Exception("Invalid AI client - missing required methods")
        
        if not hasattr(client, 'chat') or not callable(getattr(client, 'chat', None)):
            _LOGGER.error("Ollama client does not have chat method. Client type: %s, Methods: %s", 
                         type(client), dir(client))
            raise Exception("Invalid AI client - missing required methods")

        # Get available models from the client
        available_models = []
        try:
            _LOGGER.info("Attempting to list models from Ollama client...")
            available_models = await client.list_models()
            _LOGGER.info("Found %d available models on Ollama server: %s", len(available_models), [m.get("name", m.get("model", "unknown")) for m in available_models])
        except Exception as e:
            _LOGGER.error("Could not get model list from Ollama: %s", e)
            _LOGGER.error("Client type: %s, Client methods: %s", type(client), dir(client))
            raise Exception("AI model unavailable")

        if not available_models:
            _LOGGER.error("No models available on Ollama server")
            raise Exception("No AI models available")

        # Use the first available model
        model_info = available_models[0]
        model_name = model_info.get("name", model_info.get("model", "")) if isinstance(model_info, dict) else str(model_info)
        
        _LOGGER.info("Using model '%s' for AI enhancement", model_name)

        # Call Ollama API with proper format
        try:
            _LOGGER.info("Making Ollama API call with model: %s", model_name)
            _LOGGER.info("Client object: %s, Type: %s", client, type(client))
            
            response = await client.chat(
                model=model_name,
                messages=[
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                stream=False,  # Non-streaming for easier parsing
                timeout=300.0,  # 5 minute timeout for complex pattern analysis
            )
            
            _LOGGER.info("Ollama API call completed successfully")
        except httpx.HTTPStatusError as e:
            _LOGGER.error("Ollama API call failed with HTTP status %d: %s", e.response.status_code, e.response.text)
            _LOGGER.error("Server URL: %s", getattr(client, 'base_url', 'unknown'))
            _LOGGER.error("Model: %s", model_name)
            raise Exception(f"AI API call failed: Server returned {e.response.status_code} - {e.response.text}")
        except httpx.ConnectError as e:
            _LOGGER.error("Ollama API call failed - connection error: %s", e)
            _LOGGER.error("Cannot connect to Ollama server at: %s", getattr(client, 'base_url', 'unknown'))
            raise Exception(f"AI API call failed: Cannot connect to server at {getattr(client, 'base_url', 'unknown')}")
        except httpx.TimeoutException as e:
            _LOGGER.error("Ollama API call failed - timeout: %s", e)
            _LOGGER.error("Server URL: %s", getattr(client, 'base_url', 'unknown'))
            raise Exception(f"AI API call failed: Request timed out ({getattr(client, 'timeout', 120.0)}s)")
        except Exception as e:
            _LOGGER.error("Ollama API call failed: %s", e)
            _LOGGER.error("Exception type: %s", type(e).__name__)
            _LOGGER.error("Exception details: %s", str(e))
            raise Exception(f"AI API call failed: {str(e)}")

        # Parse the response
        response_text = response.get("message", {}).get("content", "")
        
        _LOGGER.info("=== AI RESPONSE ANALYSIS ===")
        _LOGGER.info("AI Response length: %d characters", len(response_text))
        _LOGGER.info("AI Response preview (first 500 chars): %s", response_text[:500])
        
        # Check if response is empty or too short
        if not response_text or len(response_text) < 10:
            _LOGGER.error("AI returned empty or too short response")
            raise Exception("AI returned empty response")

        # Parse JSON response
        _LOGGER.info("Attempting to parse AI response as JSON...")
        ai_response = self._parse_json_robust(response_text)
        
        if not ai_response or not isinstance(ai_response, dict):
            _LOGGER.error("AI response parsing failed - response was invalid JSON")
            _LOGGER.error("Raw response: %s", response_text)
            raise Exception("AI response parsing failed")

        enhanced_recommendations = ai_response.get("enhanced_recommendations", [])
        
        if not enhanced_recommendations or not isinstance(enhanced_recommendations, list):
            _LOGGER.error("AI response had empty or invalid enhanced_recommendations")
            _LOGGER.error("AI response keys: %s", list(ai_response.keys()) if ai_response else "None")
            _LOGGER.error("Enhanced recommendations type: %s", type(enhanced_recommendations))
            raise Exception("AI returned no recommendations")

        _LOGGER.info("Successfully parsed %d enhanced recommendations from AI", len(enhanced_recommendations))
        
        # Process recommendations and generate YAML templates with real entities
        for i, rec in enumerate(enhanced_recommendations):
            if isinstance(rec, dict):
                rec["ai_enhanced"] = True
                # Generate YAML template with actual entity IDs
                rec["template"] = self._generate_automation_for_pattern(rec)
                _LOGGER.info("Processed recommendation %d: %s", i+1, rec.get("title", "Untitled"))

        _LOGGER.info("AI enhanced %d automation recommendations", len(enhanced_recommendations))
        _LOGGER.info("=== OLLAMA AI DEBUGGING COMPLETE ===")
        return enhanced_recommendations

    def _normalize_json(self, text: str) -> str:
        """Normalize malformed JSON text to valid JSON."""
        import re
        
        # Remove markdown code blocks
        text = re.sub(r'```json\s*', '', text, flags=re.IGNORECASE)
        text = re.sub(r'```\s*', '', text)
        
        # Remove trailing commas before closing brackets
        text = re.sub(r',\s*([\]}])', r'\1', text)
        
        return text

    def _parse_json_robust(self, text: str) -> Optional[dict]:
        """Robustly parse JSON with multiple fallback strategies."""
        import json
        import re
        
        _LOGGER.debug("Attempting to parse JSON response (length: %d)", len(text))
        
        # Strategy 1: Direct parse
        try:
            result = json.loads(text)
            _LOGGER.debug("Strategy 1 (direct parse) succeeded")
            return result
        except json.JSONDecodeError as e:
            _LOGGER.debug("Strategy 1 failed: %s", e)
        
        # Clean up the text first
        text = text.strip()
        
        # Strategy 2: Try to find JSON in markdown code blocks
        markdown_match = re.search(r'```json\s*(\{.*?\})\s*```', text, re.DOTALL | re.IGNORECASE)
        if markdown_match:
            try:
                result = json.loads(markdown_match.group(1))
                _LOGGER.debug("Strategy 2 (markdown block) succeeded")
                return result
            except json.JSONDecodeError as e:
                _LOGGER.debug("Strategy 2 failed: %s", e)
        
        # Strategy 3: Try to find any JSON object - use bracket matching
        first_brace = text.find('{')
        if first_brace != -1:
            # Find the matching closing brace
            brace_count = 0
            end_brace = -1
            for i in range(first_brace, len(text)):
                if text[i] == '{':
                    brace_count += 1
                elif text[i] == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        end_brace = i
                        break
            
            if end_brace != -1:
                json_str = text[first_brace:end_brace+1]
                try:
                    result = json.loads(json_str)
                    _LOGGER.debug("Strategy 3 (bracket matching) succeeded")
                    return result
                except json.JSONDecodeError as e:
                    _LOGGER.debug("Strategy 3 failed: %s", e)
        
        # Strategy 4: Try to fix common issues and retry
        try:
            # Remove common issues
            cleaned = text
            # Remove trailing commas
            cleaned = re.sub(r',\s*([\]}])', r'\1', cleaned)
            # Fix single quotes around keys/values
            cleaned = re.sub(r"(\w+):", r'"\1":', cleaned)
            # Replace single quotes with double quotes for values
            cleaned = re.sub(r":\s*'([^']*)'", r': "\1"', cleaned)
            
            result = json.loads(cleaned)
            _LOGGER.debug("Strategy 4 (cleanup) succeeded")
            return result
        except json.JSONDecodeError as e:
            _LOGGER.debug("Strategy 4 failed: %s", e)
        
        # Strategy 5: Try to extract just the recommendations array
        try:
            # Find array start
            arr_match = re.search(r'\[\s*\{', text)
            if arr_match:
                # Try to find the closing bracket
                bracket_count = 0
                start_idx = arr_match.start()
                end_idx = -1
                
                for i in range(start_idx, len(text)):
                    if text[i] == '[':
                        bracket_count += 1
                    elif text[i] == ']':
                        bracket_count -= 1
                        if bracket_count == 0:
                            end_idx = i
                            break
                
                if end_idx != -1:
                    array_str = text[start_idx:end_idx+1]
                    # Wrap in object
                    wrapped = '{"enhanced_recommendations": ' + array_str + '}'
                    result = json.loads(wrapped)
                    _LOGGER.debug("Strategy 5 (array extraction) succeeded")
                    return result
        except Exception as e:
            _LOGGER.debug("Strategy 5 failed: %s", e)
        
        _LOGGER.debug("All JSON parsing strategies failed")
        _LOGGER.debug("Response preview (first 300 chars): %s", text[:300])
        return None

    def _fix_condition_key(self, condition: dict) -> dict | None:
        """Fix common AI mistakes in condition definitions.
        
        AI often confuses triggers and conditions, using 'platform' instead of 'condition'.
        Also fixes internal key names (e.g., 'weekdays' -> 'weekday').
        
        Args:
            condition: The condition dict to fix
            
        Returns:
            Fixed condition dict with correct 'condition' key and internal structure, or None if invalid
        """
        if not isinstance(condition, dict):
            return None
        
        # If it already has 'condition' key, still check internal structure
        if "condition" in condition:
            return self._fix_condition_internal_keys(condition)
        
        # If it has 'platform' key, it's likely a trigger that AI wrongly put in conditions
        if "platform" in condition:
            platform = condition.get("platform", "")
            # Map platform to condition type
            condition_map = {
                "time": "time",
                "sun": "sun",
                "state": "state",
                "numeric_state": "numeric_state",
                "template": "template",
                "zone": "zone",
                "and": "and",
                "or": "or",
                "not": "not",
            }
            condition_type = condition_map.get(platform)
            if condition_type:
                # Convert trigger format to condition format
                fixed = {"condition": condition_type}
                # Copy all other keys (except platform) with fixes
                for key, value in condition.items():
                    if key != "platform":
                        fixed[key] = value
                
                # Fix internal keys for the specific condition type
                fixed = self._fix_condition_internal_keys(fixed)
                
                _LOGGER.debug("Fixed condition: changed platform=%s to condition=%s", platform, condition_type)
                return fixed
            else:
                _LOGGER.warning("Unknown platform in condition: %s", platform)
                return None
        
        # If neither key is present, return as-is but log warning
        _LOGGER.warning("Condition dict has neither 'condition' nor 'platform' key: %s", condition)
        return condition
    
    def _fix_condition_internal_keys(self, condition: dict) -> dict:
        """Fix internal keys within a condition that AI often gets wrong.
        
        For example, time conditions use 'weekday' not 'weekdays', etc.
        """
        if not isinstance(condition, dict):
            return condition
        
        fixed = dict(condition)
        
        # Time condition: weekdays -> weekday (singular)
        if fixed.get("condition") == "time":
            if "weekdays" in fixed:
                fixed["weekday"] = fixed.pop("weekdays")
            # Also handle 'at' -> 'after'/'before' for time conditions if needed
        
        # Sun condition: could have trigger-specific keys
        elif fixed.get("condition") == "sun":
            # Remove any trigger-specific keys that don't apply to conditions
            pass
        
        # State condition: similar adjustments if needed
        elif fixed.get("condition") == "state":
            # Remove trigger-specific keys like 'from', 'to' that don't apply to conditions
            pass
        
        return fixed

    def _generate_automation_for_pattern(self, pattern: dict) -> str:
        """Generate Home Assistant automation YAML for a specific pattern.
        
        Uses AI-provided trigger/action/condition if available, otherwise generates based on type.
        """
        import yaml
        
        # Get values from pattern - AI returns 'type' not 'automation_type'
        automation_type = pattern.get("type", "") or pattern.get("automation_type", "")
        title = pattern.get("title", "")
        description = pattern.get("description", "")
        confidence = pattern.get("confidence", 0)
        entities = pattern.get("entities", [])
        
        # Get AI-provided trigger, action, condition (if any)
        ai_trigger = pattern.get("trigger")
        ai_action = pattern.get("action")
        ai_condition = pattern.get("condition")
        
        # Generate a unique alias
        safe_title = "".join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in title)
        alias = f"Oasira-{safe_title.replace(' ', '-')}"
        
        # Try to use AI-provided trigger/action/condition directly
        if ai_trigger and ai_action:
            try:
                # Normalize trigger and action to lists
                trigger_list = ai_trigger if isinstance(ai_trigger, list) else [ai_trigger]
                action_list = ai_action if isinstance(ai_action, list) else [ai_action]
                
                # Validate and normalize condition - fix AI's common mistake of using 'platform' instead of 'condition'
                conditions_list = []
                if ai_condition:
                    if isinstance(ai_condition, dict):
                        # Fix common AI mistake: conditions use 'condition' key, not 'platform'
                        fixed_condition = _fix_condition_key(ai_condition)
                        if fixed_condition:
                            conditions_list = [fixed_condition]
                    elif isinstance(ai_condition, list):
                        for cond in ai_condition:
                            if isinstance(cond, dict):
                                fixed_cond = _fix_condition_key(cond)
                                if fixed_cond:
                                    conditions_list.append(fixed_cond)
                
                # Build automation dict with AI-provided structure
                automation_dict = {
                    "alias": alias,
                    "description": f"{description} (AI-generated, confidence: {confidence:.0%})",
                    "trigger": trigger_list,
                    "action": action_list,
                    "mode": "single",
                }
                
                # Only add condition key if we have valid conditions
                if conditions_list:
                    automation_dict["condition"] = conditions_list
                
                # Add ID if not present
                if "id" not in automation_dict:
                    import uuid
                    automation_dict["id"] = f"'{uuid.uuid4()}'"
                
                # Convert to YAML string
                yaml_str = yaml.dump(automation_dict, default_flow_style=False, sort_keys=False, allow_unicode=True)
                _LOGGER.debug("Generated YAML from AI trigger/action for %s", alias)
                return yaml_str
            except Exception as e:
                _LOGGER.debug("Could not use AI trigger/action for %s: %s - will generate fallback", alias, e)
        
        # Fallback: Generate based on automation type
        _LOGGER.debug("Generating fallback automation for %s (type: %s)", alias, automation_type)
        
        # Build trigger and action based on type
        trigger = []
        action = []
        condition = []
        
        if automation_type == "light_schedule":
            start_time = pattern.get("start_time", "08:00")
            end_time = pattern.get("end_time", "23:00")
            day_type = pattern.get("day_type", "daily")
            
            trigger = [
                {"platform": "time", "at": start_time},
                {"platform": "time", "at": end_time}
            ]
            action = [
                {"service": "homeassistant.turn_on", "target": {"entity_id": entities[:3] if entities else ["light.example"]}},
                {"service": "homeassistant.turn_off", "target": {"entity_id": entities[:3] if entities else ["light.example"]}}
            ]
            if day_type == "weekday":
                condition = [{"platform": "time", "weekdays": ["mon", "tue", "wed", "thu", "fri"]}]
                
        elif automation_type == "motion_light":
            motion_sensors = [e for e in entities if "motion" in e.lower()] if entities else []
            lights = [e for e in entities if e.startswith(("light.", "switch."))] if entities else []
            
            trigger = [{"platform": "state", "entity_id": motion_sensors[0] if motion_sensors else "binary_sensor.example", "to": "on"}]
            action = [{"service": "light.turn_on", "target": {"entity_id": lights[:3] if lights else ["light.example"]}}]
            
        elif automation_type == "presence_automation":
            presence_entities = [e for e in entities if any(p in e for p in ["person.", "device_tracker.", "binary_sensor.presence"])] if entities else []
            trigger = [{"platform": "state", "entity_id": presence_entities[0] if presence_entities else "person.example", "to": "home"}]
            action = [{"service": "homeassistant.turn_on", "target": {"entity_id": entities[:5] if entities else ["switch.example"]}}]
            
        elif automation_type == "climate_schedule":
            trigger = [{"platform": "time", "at": pattern.get("start_time", "07:00")}]
            action = [{"service": "climate.set_temperature", "target": {"entity_id": entities[:1] if entities else ["climate.example"]}, "data": {"temperature": 20}}]
            
        elif automation_type == "sensor_trigger":
            sensor_entities = [e for e in entities if e.startswith(("sensor.", "binary_sensor."))] if entities else []
            trigger = [{"platform": "state", "entity_id": sensor_entities[0] if sensor_entities else "sensor.example"}]
            action = [{"service": "notify.persistent_notification", "data": {"message": f"Sensor triggered: {sensor_entities[0] if sensor_entities else 'unknown'}"}}]
            
        elif automation_type == "energy_saving":
            trigger = [{"platform": "time", "at": "23:00:00"}]
            action = [{"service": "switch.turn_off", "target": {"entity_id": entities[:5] if entities else ["switch.example"]}}]
            
        else:
            # Generic fallback
            trigger = [{"platform": "homeassistant", "event": "start"}]
            action = [{"service": "persistent_notification.create", "data": {"message": f"Automation {alias} triggered"}}]
        
        # Build automation dict - only include condition if it's not empty
        automation_dict = {
            "alias": alias,
            "description": f"{description} (Auto-generated, confidence: {confidence:.0%})",
            "trigger": trigger,
            "action": action,
            "mode": "single",
        }
        
        # Only add condition if it's not empty
        if condition:
            automation_dict["condition"] = condition
        
        import uuid
        automation_dict["id"] = f"'{uuid.uuid4()}'"
        
        # Convert to YAML string
        yaml_str = yaml.dump(automation_dict, default_flow_style=False, sort_keys=False, allow_unicode=True)
        return yaml_str

    def _generate_summary(
        self, 
        patterns: list[dict], 
        recommendations: list[dict], 
        entities: list[dict]
    ) -> dict[str, Any]:
        """Generate analysis summary."""
        return {
            "total_patterns": len(patterns),
            "total_recommendations": len(recommendations),
            "analyzed_entities": len(entities),
            "pattern_types": list(set(p.get("pattern_type", "") for p in patterns)),
            "recommendation_types": list(set(r.get("type", "") for r in recommendations)),
            "highest_confidence": max([p.get("confidence", 0) for p in patterns]) if patterns else 0,
            "summary_text": f"Analyzed {len(entities)} entities over {DEFAULT_ANALYSIS_DAYS} days, found {len(patterns)} usage patterns and generated {len(recommendations)} automation recommendations."
        }

    def _generate_automation_yaml(self, recommendations: list[dict]) -> str:
        """Generate YAML automation definitions from recommendations."""
        yaml_lines = [
            "# Home Assistant Automations - Generated by Oasira AI Conversation",
            "# These automations are based on YOUR actual device usage patterns",
            "# Review and test before enabling",
            "",
        ]
        
        for i, rec in enumerate(recommendations, 1):
            yaml_lines.append(f"# Automation {i}: {rec.get('title', 'Untitled')}")
            yaml_lines.append(f"# Confidence: {rec.get('confidence', 0):.0%}")
            yaml_lines.append(f"# Description: {rec.get('description', '')}")
            
            # Get actual entity info
            source_entity = rec.get("source_entity", rec.get("entities", ["unknown"])[0] if rec.get("entities") else "unknown")
            yaml_lines.append(f"# Source Entity: {source_entity}")
            
            template = rec.get("template", "")
            if template:
                # Use the pre-generated template with real entity IDs
                yaml_lines.append(template)
            else:
                # Generate a template from pattern data
                pattern_template = self._generate_automation_for_pattern(rec)
                yaml_lines.append(pattern_template)
            
            yaml_lines.append("")
        
        return "\n".join(yaml_lines)
