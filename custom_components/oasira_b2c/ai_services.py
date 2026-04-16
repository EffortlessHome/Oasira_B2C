"""Services for the Oasira AI Conversation component."""

import base64
import logging
import mimetypes
from pathlib import Path
from urllib.parse import urlparse

import httpx
import voluptuous as vol

from homeassistant.const import CONF_API_KEY
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError
from typing import Any

from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .ai_const import (
    CONF_BASE_URL,
    CONF_MAX_TOKENS,
    CONF_MODEL,
    DEFAULT_CONF_BASE_URL,
    DEFAULT_MODEL,
    DOMAIN,
    GITHUB_REPO_NAME,
    GITHUB_REPO_OWNER,
    GITHUB_SKILLS_BRANCH,
    GITHUB_SKILLS_PATH,
    SERVICE_DOWNLOAD_SKILL,
    SERVICE_RELOAD_SKILLS,
)

ANALYZE_IMAGE_SCHEMA = vol.Schema(
    {
        vol.Required("image_path"): cv.string,
        vol.Optional("prompt", default="Please describe this image in detail."): cv.string,
    }
)

SCAN_HOME_AUTOMATION_PATTERNS_SCHEMA = vol.Schema(
    {
        vol.Optional("time_range_days", default=7): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=90)
        ),
        vol.Optional("entity_types", default=[]): [str],
        vol.Optional("include_entities", default=[]): [str],
        vol.Optional("exclude_entities", default=[]): [str],
        vol.Optional("pattern_types", default=[]): [str],
        vol.Optional("min_confidence", default=0.7): vol.All(
            vol.Coerce(float), vol.Range(min=0.0, max=1.0)
        ),
        vol.Optional("time_window_minutes", default=30): vol.All(
            vol.Coerce(int), vol.Range(min=5, max=1440)
        ),
        vol.Optional("save_results", default=False): cv.boolean,
        vol.Optional("results_file", default="automation_analysis_results.yaml"): cv.string,
        vol.Optional("create_automations", default=True): cv.boolean,
        vol.Optional("automations_file", default="automations.yaml"): cv.string,
    }
)

CHANGE_CONFIG_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_BASE_URL): cv.string,
        vol.Optional(CONF_MODEL): cv.string,
    }
)

RELOAD_SKILLS_SCHEMA = vol.Schema({})

DOWNLOAD_SKILL_SCHEMA = vol.Schema(
    {
        vol.Required("skill_name"): cv.string,
    }
)

_LOGGER = logging.getLogger(__package__)


def _get_ollama_client(hass: HomeAssistant) -> Any:
    """Get the Ollama client from the first available config entry.

    Returns the client or raises HomeAssistantError if none found.
    """
    config_entries = hass.config_entries.async_entries(DOMAIN)
    for entry in config_entries:
        if hasattr(entry, "oasira_ai_runtime_data") and entry.oasira_ai_runtime_data:
            return entry.oasira_ai_runtime_data
        if hasattr(entry, 'runtime_data') and entry.runtime_data:
            return entry.runtime_data
    raise HomeAssistantError("No Oasira AI config entry with active connection found")


def _get_integration_settings(hass: HomeAssistant) -> dict[str, Any]:
    """Get integration settings from the first available config entry.

    Returns a dict with 'model' and 'max_tokens' keys, falling back to defaults.
    """
    settings = {
        "model": DEFAULT_MODEL,
        "max_tokens": 300,  # Default for vision tasks
    }

    config_entries = hass.config_entries.async_entries(DOMAIN)
    for entry in config_entries:
        # Check subentries for conversation or ai_task_data
        if hasattr(entry, 'subentries') and entry.subentries:
            for subentry in entry.subentries.values():
                if hasattr(subentry, 'data'):
                    subentry_data = subentry.data
                    # Get model from subentry
                    if CONF_MODEL in subentry_data:
                        settings["model"] = subentry_data[CONF_MODEL]
                    # Get max_tokens from subentry (for vision tasks, we use a reasonable default)
                    if CONF_MAX_TOKENS in subentry_data:
                        settings["max_tokens"] = subentry_data[CONF_MAX_TOKENS]
                    break  # Use first subentry found
            if settings["model"] != DEFAULT_MODEL or settings["max_tokens"] != 300:
                break  # Found valid settings

    _LOGGER.debug("Integration settings: %s", settings)
    return settings


async def async_setup_services(hass: HomeAssistant, config: ConfigType) -> None:
    """Set up services for the Ollama conversation component."""

    async def change_config(call: ServiceCall) -> None:
        """Change configuration."""
        # Get the first available config entry
        config_entries = hass.config_entries.async_entries(DOMAIN)
        if not config_entries:
            raise HomeAssistantError("No Oasira AI config entry found")

        entry = config_entries[0]
        entry_id = entry.entry_id

        updates = {}
        for key in (CONF_BASE_URL, CONF_MODEL):
            if key in call.data:
                updates[key] = call.data[key]

        if not updates:
            return

        new_data = entry.data.copy()
        new_data.update(updates)

        _LOGGER.debug("Updating config entry %s with %s", entry_id, new_data)

        hass.config_entries.async_update_entry(entry, data=new_data)

    async def reload_skills(call: ServiceCall) -> ServiceResponse:
        """Reload skills from the user skill directory."""
        from .ai_skills import SkillManager

        skill_manager = await SkillManager.async_get_instance(hass)
        await skill_manager.async_load_skills()

        return {
            "loaded_skills": len(skill_manager.get_all_skills()),
        }

    async def download_skill(call: ServiceCall) -> ServiceResponse:
        """Download a skill from the GitHub repository."""
        from .ai_skills import SkillManager

        skill_name = call.data["skill_name"]
        session = async_get_clientsession(hass)

        # Fetch skill directory contents from GitHub API
        api_url = (
            f"https://api.github.com/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}"
            f"/contents/{GITHUB_SKILLS_PATH}/{skill_name}"
            f"?ref={GITHUB_SKILLS_BRANCH}"
        )

        downloaded_files: list[str] = []

        async def _download_directory(url: str, local_dir: Path) -> None:
            """Recursively download a directory from GitHub."""
            async with session.get(url) as resp:
                if resp.status == 404:
                    raise HomeAssistantError(
                        f"Skill `{skill_name}` not found in repository"
                    )
                if resp.status != 200:
                    raise HomeAssistantError(
                        f"Failed to fetch skill from GitHub (HTTP {resp.status})"
                    )
                items = await resp.json()

            if not isinstance(items, list):
                raise HomeAssistantError(
                    f"Unexpected response from GitHub for skill `{skill_name}`"
                )

            for item in items:
                item_path = local_dir / item["name"]
                if item["type"] == "file":
                    # Download file content
                    async with session.get(item["download_url"]) as file_resp:
                        if file_resp.status != 200:
                            raise HomeAssistantError(
                                f"Failed to download `{item['path']}`"
                            )
                        content = await file_resp.read()

                    await hass.async_add_executor_job(
                        _write_file_sync, item_path, content
                    )
                    downloaded_files.append(str(item["path"]))
                elif item["type"] == "dir":
                    # Recurse into subdirectory
                    await _download_directory(item["url"], item_path)

        def _write_file_sync(file_path: Path, content: bytes) -> None:
            """Write file content to disk (run in executor)."""
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_bytes(content)

        # Determine target directory
        skill_manager = await SkillManager.async_get_instance(hass)
        target_dir = skill_manager.user_skills_dir / skill_name

        _LOGGER.info("Downloading skill `%s` to %s", skill_name, target_dir)

        try:
            await _download_directory(api_url, target_dir)
        except HomeAssistantError:
            raise
        except Exception as err:
            raise HomeAssistantError(
                f"Failed to download skill `{skill_name}`: {err}"
            ) from err

        # Reload skills after download
        await skill_manager.async_load_skills()

        _LOGGER.info(
            "Successfully downloaded skill `%s` (%d files)",
            skill_name,
            len(downloaded_files),
        )

        return {
            "skill_name": skill_name,
            "downloaded_files": downloaded_files,
            "target_directory": str(target_dir),
        }

    async def analyze_image(call: ServiceCall) -> ServiceResponse:
        """Analyze an image and provide detailed description."""
        try:
            # Get configuration
            image_path = call.data["image_path"]
            prompt = call.data["prompt"]

            # Always use integration configured model
            settings = _get_integration_settings(hass)
            model = settings["model"]
            _LOGGER.debug("Using integration model for analysis: %s", model)

            # Get Ollama client
            client = _get_ollama_client(hass)

            # Convert local image path to base64
            image_data = _local_image_to_base64(hass, image_path)

            # Create messages for vision model
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": image_data["url"]},
                        },
                    ],
                }
            ]

            # Call the vision model API
            response = await client.chat(
                model=model,
                messages=messages,
                stream=False,
            )

            # Extract the analysis
            analysis = response.get("message", {}).get("content", "")

            _LOGGER.info("Analyzed image with prompt: %s", prompt)

            response_dict = {
                "image_path": image_path,
                "prompt": prompt,
                "model": model,
                "analysis": analysis,
            }

        except httpx.HTTPError as err:
            raise HomeAssistantError(f"Error analyzing image: {err}") from err

        return response_dict

    async def scan_home_automation_patterns(call: ServiceCall) -> ServiceResponse:
        """Trigger an automated scan of Home Assistant history data to analyze usage patterns."""
        try:
            # Get configuration
            time_range_days = call.data["time_range_days"]
            entity_types = call.data["entity_types"]
            include_entities = call.data["include_entities"]
            exclude_entities = call.data["exclude_entities"]
            pattern_types = call.data["pattern_types"]
            min_confidence = call.data["min_confidence"]
            time_window_minutes = call.data["time_window_minutes"]
            save_results = call.data["save_results"]
            results_file = call.data["results_file"]
            create_automations = call.data["create_automations"]
            automations_file = call.data["automations_file"]

            # Get exposed entities
            from .ai_helpers import get_exposed_entities
            exposed_entities = get_exposed_entities(hass)

            # Create automation analysis function instance
            from .ai_functions.automation_analysis import AutomationAnalysisFunction
            automation_func = AutomationAnalysisFunction()

            # Prepare function configuration
            function_config = {
                "type": "automation_analysis",
                "time_range_days": time_range_days,
                "entity_types": entity_types,
                "include_entities": include_entities,
                "exclude_entities": exclude_entities,
                "pattern_types": pattern_types,
                "min_confidence": min_confidence,
                "time_window_minutes": time_window_minutes,
            }

            # Try to get Ollama client from the integration's config entries
            client = None
            try:
                # Get the first available Oasira AI config entry
                config_entries = hass.config_entries.async_entries(DOMAIN)
                _LOGGER.info("Found %d Oasira AI config entries", len(config_entries))
                
                for config_entry in config_entries:
                    _LOGGER.debug("Checking config entry %s (state: %s)", config_entry.entry_id, config_entry.state)
                    if hasattr(config_entry, "oasira_ai_runtime_data") and config_entry.oasira_ai_runtime_data:
                        client = config_entry.oasira_ai_runtime_data
                        _LOGGER.info("Using merged AI client from config entry %s for AI-enhanced automation analysis", config_entry.entry_id)
                        _LOGGER.debug("Client type: %s, client has 'chat' method: %s", type(client).__name__, hasattr(client, 'chat'))
                        break
                    if hasattr(config_entry, 'runtime_data') and config_entry.runtime_data:
                        client = config_entry.runtime_data
                        _LOGGER.info("Using Ollama client from Oasira AI config entry %s for AI-enhanced automation analysis", config_entry.entry_id)
                        _LOGGER.debug("Client type: %s, client has 'chat' method: %s", type(client).__name__, hasattr(client, 'chat'))
                        break
                    else:
                        _LOGGER.debug("Config entry %s has no runtime_data or it's None", config_entry.entry_id)
                        
                if client is None:
                    _LOGGER.warning("No valid Ollama client found in config entries - AI enhancement will be skipped")
                    _LOGGER.debug("Config entry details: %s", [(e.entry_id, e.state, hasattr(e, 'runtime_data'), getattr(e, 'runtime_data', None) is not None) for e in config_entries])
            except Exception as e:
                _LOGGER.error("Error accessing config entry client: %s", e)

            # Execute the analysis
            _LOGGER.info("Executing automation analysis with client=%s", "provided" if client else "None")
            analysis_result = await automation_func.execute(
                hass=hass,
                function_config=function_config,
                arguments={},
                exposed_entities=exposed_entities,
                client=client,  # Pass client for optional AI enhancement
            )
            _LOGGER.info("Automation analysis completed - AI enhanced: %s", analysis_result.get("analysis_parameters", {}).get("ai_enhanced", False))

            _LOGGER.info(
                "Completed automation pattern scan: %d patterns found, %d recommendations generated (AI enhanced: %s)",
                len(analysis_result.get("patterns", [])),
                len(analysis_result.get("recommendations", [])),
                analysis_result.get("analysis_parameters", {}).get("ai_enhanced", False),
            )
            
            # Debug the recommendations
            _LOGGER.debug("Analysis result details:")
            _LOGGER.debug("Patterns: %s", analysis_result.get("patterns", []))
            _LOGGER.debug("Recommendations: %s", analysis_result.get("recommendations", []))
            _LOGGER.debug("Analysis parameters: %s", analysis_result.get("analysis_parameters", {}))

            # Create automations if requested
            created_automations = []
            automation_suggestions = []
            if create_automations:
                created_automations = await _create_automations_from_recommendations(
                    hass, analysis_result.get("recommendations", []), automations_file
                )
                _LOGGER.info("Created %d automations from recommendations", len(created_automations))

            # Generate automation suggestions for all recommendations (even if not saved to file)
            # This ensures AI-generated automation templates are shown in service results
            recommendations = analysis_result.get("recommendations", [])
            _LOGGER.info("Processing %d recommendations for automation suggestions", len(recommendations))
            
            # Check if recommendations are empty and provide better error message
            if not recommendations:
                _LOGGER.warning("No recommendations found - this could indicate a parsing error in the AI response")
                _LOGGER.debug("Analysis result details: %s", analysis_result)
            for i, recommendation in enumerate(analysis_result.get("recommendations", [])):
                try:
                    _LOGGER.debug("Processing recommendation %d: %s", i + 1, recommendation.get("title", "No title"))
                    _LOGGER.debug("Recommendation details: %s", recommendation)
                    # Generate automation from recommendation
                    automation = _generate_automation_from_recommendation(
                        recommendation, i + 1
                    )
                    
                    if automation:
                        _LOGGER.debug("Generated automation %d: %s", i + 1, automation.get("alias", "No alias"))
                        _LOGGER.debug("Automation structure: %s", automation)
                        # Add the full automation structure to suggestions
                        automation_suggestions.append(automation)
                    else:
                        _LOGGER.debug("No automation generated for recommendation %d", i + 1)
                        
                except Exception as e:
                    _LOGGER.error("Failed to generate automation suggestion for recommendation %d: %s", i + 1, e)
                    continue

            # Save results to file if requested
            if save_results:
                import yaml
                from homeassistant.util import dt as dt_util

                # Add timestamp to results
                results_with_metadata = {
                    "scan_timestamp": dt_util.now().isoformat(),
                    "scan_parameters": {
                        "time_range_days": time_range_days,
                        "entity_types": entity_types,
                        "include_entities": include_entities,
                        "exclude_entities": exclude_entities,
                        "pattern_types": pattern_types,
                        "min_confidence": min_confidence,
                        "time_window_minutes": time_window_minutes,
                        "ai_enhanced": analysis_result.get("analysis_parameters", {}).get("ai_enhanced", False),
                        "create_automations": create_automations,
                        "automations_file": automations_file if create_automations else None,
                    },
                    "created_automations": created_automations,
                    "automation_suggestions": automation_suggestions,  # Include suggestions in file
                    **analysis_result,
                }

                # Write to file
                results_path = Path(hass.config.config_dir) / results_file
                await hass.async_add_executor_job(
                    _write_yaml_file, results_path, results_with_metadata
                )

                _LOGGER.info("Saved scan results to: %s", results_path)

                # Add file path to response
                analysis_result["results_file"] = str(results_path)
                analysis_result["created_automations"] = created_automations

            response_dict = {
                "scan_parameters": {
                    "time_range_days": time_range_days,
                    "entity_types": entity_types,
                    "include_entities": include_entities,
                    "exclude_entities": exclude_entities,
                    "pattern_types": pattern_types,
                    "min_confidence": min_confidence,
                    "time_window_minutes": time_window_minutes,
                    "save_results": save_results,
                    "results_file": results_file if save_results else None,
                    "ai_enhanced": analysis_result.get("analysis_parameters", {}).get("ai_enhanced", False),
                    "create_automations": create_automations,
                },
                **analysis_result,
                "automation_suggestions": automation_suggestions,  # Include suggestions in response
            }

        except Exception as err:
            _LOGGER.error("Error during automation pattern scan: %s", err, exc_info=True)
            raise HomeAssistantError(f"Error during automation pattern scan: {err}") from err

        return response_dict

    hass.services.async_register(
        DOMAIN,
        "change_config",
        change_config,
        schema=CHANGE_CONFIG_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_RELOAD_SKILLS,
        reload_skills,
        schema=RELOAD_SKILLS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_DOWNLOAD_SKILL,
        download_skill,
        schema=DOWNLOAD_SKILL_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )

    hass.services.async_register(
        DOMAIN,
        "analyze_image",
        analyze_image,
        schema=ANALYZE_IMAGE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )

    hass.services.async_register(
        DOMAIN,
        "scan_home_automation_patterns",
        scan_home_automation_patterns,
        schema=SCAN_HOME_AUTOMATION_PATTERNS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )


def to_image_param(hass: HomeAssistant, image: dict) -> dict:
    """Convert url to base64 encoded image if local."""
    url = image["url"]

    if urlparse(url).scheme in cv.EXTERNAL_URL_PROTOCOL_SCHEMA_LIST:
        return image

    if not hass.config.is_allowed_path(url):
        raise HomeAssistantError(
            f"Cannot read `{url}`, no access to path; "
            "`allowlist_external_dirs` may need to be adjusted in "
            "`configuration.yaml`"
        )
    if not Path(url).exists():
        raise HomeAssistantError(f"`{url}` does not exist")
    mime_type, _ = mimetypes.guess_type(url)
    if mime_type is None or not mime_type.startswith("image"):
        raise HomeAssistantError(f"`{url}` is not an image")

    image["url"] = f"data:{mime_type};base64,{encode_image(url)}"
    return image


def _local_image_to_base64(hass: HomeAssistant, image_path: str) -> dict:
    """Convert a local image file path to base64 encoded data URI.

    Args:
        hass: Home Assistant instance
        image_path: Local file path to the image

    Returns:
        Dict with 'url' key containing the data URI

    Raises:
        HomeAssistantError: If the file cannot be read or is not an image
    """
    # Check if path is allowed
    if not hass.config.is_allowed_path(image_path):
        raise HomeAssistantError(
            f"Cannot read `{image_path}`, no access to path; "
            "`allowlist_external_dirs` may need to be adjusted in "
            "`configuration.yaml`"
        )

    # Check if file exists
    if not Path(image_path).exists():
        raise HomeAssistantError(f"`{image_path}` does not exist")

    # Check if it's an image file
    mime_type, _ = mimetypes.guess_type(image_path)
    if mime_type is None or not mime_type.startswith("image"):
        raise HomeAssistantError(f"`{image_path}` is not an image")

    # Encode to base64 and return as data URI
    return {"url": f"data:{mime_type};base64,{encode_image(image_path)}"}


def encode_image(image_path: str) -> str:
    """Convert to base64 encoded image."""
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode("utf-8")


def _write_yaml_file(file_path: Path, data: dict) -> None:
    """Write data to YAML file (run in executor)."""
    import yaml
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, sort_keys=False, indent=2)


# Keep remaining helper functions for scan_home_automation_patterns
async def _create_automations_from_recommendations(
    hass: HomeAssistant, recommendations: list[dict], automations_file: str
) -> list[dict]:
    """Create Home Assistant automations from analysis recommendations."""
    created_automations = []
    
    if not recommendations:
        _LOGGER.warning("No recommendations provided - cannot create automations")
        return created_automations

    _LOGGER.info("Starting automation creation from %d recommendations", len(recommendations))

    automations_path = Path(hass.config.config_dir) / automations_file
    _LOGGER.info("Automations file path: %s", automations_path)
    
    existing_automations = []
    if automations_path.exists():
        try:
            import yaml
            with open(automations_path, "r", encoding="utf-8") as f:
                existing_automations = yaml.safe_load(f) or []
            _LOGGER.info("Loaded %d existing automations from %s", len(existing_automations), automations_path)
        except Exception as e:
            _LOGGER.warning("Failed to load existing automations from %s: %s - starting fresh", automations_path, e)
            existing_automations = []
    else:
        _LOGGER.info("No existing automations file found at %s - will create new", automations_path)

    for i, recommendation in enumerate(recommendations):
        try:
            _LOGGER.debug("Processing recommendation %d/%d: %s", i + 1, len(recommendations), recommendation.get("title", "No title"))
            
            automation = _generate_automation_from_recommendation(
                recommendation, i + 1
            )
            
            if automation:
                existing_aliases = {auto.get("alias", "") for auto in existing_automations}
                if automation["alias"] in existing_aliases:
                    _LOGGER.info("Skipping duplicate automation: %s", automation["alias"])
                    continue
                
                existing_automations.append(automation)
                created_automations.append({
                    "alias": automation["alias"],
                    "type": recommendation.get("type", ""),
                    "description": recommendation.get("description", ""),
                    "confidence": recommendation.get("confidence", 0),
                    "complexity": recommendation.get("complexity", "Medium"),
                    "ai_enhanced": recommendation.get("ai_enhanced", False),
                })
                
                _LOGGER.info("✓ Created automation: %s (type: %s)", automation["alias"], recommendation.get("type", "unknown"))
                
        except Exception as e:
            _LOGGER.error("Failed to create automation from recommendation %d: %s", i + 1, e)
            continue

    _LOGGER.info("Generated %d automations from recommendations", len(created_automations))

    if created_automations:
        try:
            automations_path.parent.mkdir(parents=True, exist_ok=True)
            
            await hass.async_add_executor_job(
                _write_automations_file, automations_path, existing_automations
            )
            _LOGGER.info("✓ Saved %d total automations to %s", len(existing_automations), automations_path)
            
            try:
                await hass.services.async_call(
                    "automation", 
                    "reload",
                    blocking=True
                )
                _LOGGER.info("✓ Successfully reloaded Home Assistant automations - %d automations now active", len(existing_automations))
            except Exception as reload_err:
                _LOGGER.warning("Failed to reload automations via service: %s", reload_err)
                _LOGGER.info("Automations have been saved to file but may require manual reload", automations_path)
                
        except Exception as e:
            _LOGGER.error("Failed to save automations to %s: %s", automations_path, e)
            _LOGGER.error("Automations were generated but could not be saved to file")
    else:
        _LOGGER.warning("No automations were created - check recommendations and try again")

    _LOGGER.info("Automation creation complete: %d/%d recommendations converted to automations", 
                 len(created_automations), len(recommendations))
    
    return created_automations


def _generate_automation_from_recommendation(recommendation: dict, index: int) -> dict | None:
    """Generate a Home Assistant automation from a recommendation."""
    if not recommendation:
        _LOGGER.warning("Empty recommendation provided for index %d", index)
        return None
    
    automation_type = recommendation.get("type", "")
    title = recommendation.get("title", "") or f"Automation {index}"
    description = recommendation.get("description", "")
    entities = recommendation.get("entities", [])
    template = recommendation.get("template", "")
    confidence = recommendation.get("confidence", 0)
    complexity = recommendation.get("complexity", "Medium")
    ai_enhanced = recommendation.get("ai_enhanced", False)
    
    _LOGGER.debug("Generating automation: type=%s, title=%s, ai_enhanced=%s, entities=%s", 
                  automation_type, title, ai_enhanced, entities)
    
    safe_title = "".join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in title)
    alias = f"Oasira-{safe_title.replace(' ', '-')}-{index}"
    
    parsed_automation = None
    if template and ai_enhanced:
        try:
            parsed_automation = _parse_ai_template(template, alias)
            if parsed_automation:
                _LOGGER.debug("Successfully parsed AI template for %s", alias)
        except Exception as e:
            _LOGGER.debug("Could not parse AI template for %s: %s", alias, e)
    
    if parsed_automation:
        return parsed_automation
    
    ai_trigger = recommendation.get("trigger")
    ai_action = recommendation.get("action")
    ai_condition = recommendation.get("condition")
    
    if ai_trigger and ai_action:
        _LOGGER.info("Using AI-provided trigger and action for %s", alias)
        import uuid
        
        conditions_list = []
        if ai_condition and isinstance(ai_condition, dict) and ai_condition:
            conditions_list = [ai_condition]
        
        try:
            confidence_value = float(confidence)
        except (ValueError, TypeError):
            confidence_value = 0.0
        
        automation = {
            "id": f"'{uuid.uuid4()}'",
            "alias": alias,
            "description": f"{description} (AI-generated, confidence: {confidence_value:.2f})",
            "trigger": ai_trigger if isinstance(ai_trigger, list) else [ai_trigger],
            "condition": conditions_list,
            "action": ai_action if isinstance(ai_action, list) else [ai_action],
            "mode": "single",
            "variables": {
                "auto_generated": True,
                "recommendation_type": automation_type,
                "confidence": confidence_value,
                "complexity": complexity,
                "ai_enhanced": True,
                "original_entities": entities,
            }
        }
        return automation
    
    _LOGGER.debug("Generating basic automation structure for %s (type: %s)", alias, automation_type)
    
    import uuid
    
    try:
        confidence_value = float(confidence)
    except (ValueError, TypeError):
        confidence_value = 0.0
    
    automation = {
        "id": f"'{uuid.uuid4()}'",
        "alias": alias,
        "description": f"{description} (Auto-generated, confidence: {confidence_value:.2f}, complexity: {complexity})",
        "trigger": [],
        "action": [],
        "mode": "single",
    }
    
    if automation_type == "light_schedule":
        automation["trigger"] = [{"platform": "time", "at": "08:00:00"}]
        automation["action"] = [{"service": "light.turn_on", "target": {"entity_id": entities[:3] if entities else ["light.example"]}}]
    
    elif automation_type == "motion_light":
        motion_sensors = [e for e in entities if "motion" in e.lower()] if entities else []
        lights = [e for e in entities if e.startswith(("light.", "switch."))] if entities else []
        
        if motion_sensors:
            automation["trigger"] = [{"platform": "state", "entity_id": motion_sensors[:3], "to": "on"}]
        else:
            automation["trigger"] = [{"platform": "event", "event_type": "call_service", "domain": "light"}]
        
        if lights:
            automation["action"] = [{"service": "light.turn_on", "target": {"entity_id": lights[:3]}}]
        else:
            automation["action"] = [{"service": "light.turn_on", "target": {"entity_id": ["light.example"]}}]
    
    elif automation_type == "climate_schedule":
        automation["trigger"] = [{"platform": "time", "at": "07:00:00"}]
        automation["action"] = [{"service": "climate.set_temperature", "target": {"entity_id": entities[:1] if entities else ["climate.example"]}, "data": {"temperature": 20}}]
    
    elif automation_type == "presence_automation":
        presence_entities = [e for e in entities if any(prefix in e for prefix in ["person.", "device_tracker.", "binary_sensor.presence"])] if entities else []
        if presence_entities:
            automation["trigger"] = [{"platform": "state", "entity_id": presence_entities[:3], "to": "home"}]
        else:
            automation["trigger"] = [{"platform": "homeassistant", "event": "start"}]
        automation["action"] = [{"service": "homeassistant.turn_on", "target": {"entity_id": entities[:3] if entities else ["switch.example"]}}]
    
    elif automation_type == "sensor_trigger":
        sensor_entities = [e for e in entities if e.startswith(("sensor.", "binary_sensor."))] if entities else []
        if sensor_entities:
            automation["trigger"] = [{"platform": "state", "entity_id": sensor_entities[:3]}]
        else:
            automation["trigger"] = [{"platform": "event", "event_type": "state_changed"}]
        automation["action"] = [{"service": "notify.persistent_notification", "data": {"message": f"Sensor triggered: {sensor_entities[0] if sensor_entities else 'unknown'}"}}]
    
    elif automation_type == "energy_saving":
        automation["trigger"] = [{"platform": "time", "at": "23:00:00"}, {"platform": "time", "at": "06:00:00"}]
        automation["action"] = [{"service": "switch.turn_off", "target": {"entity_id": entities[:5] if entities else ["switch.example"]}}]
    
    else:
        if entities:
            automation["trigger"] = [{"platform": "state", "entity_id": entities[:1]}]
            automation["action"] = [{"service": "homeassistant.turn_on", "target": {"entity_id": entities}}]
        else:
            automation["trigger"] = [{"platform": "homeassistant", "event": "start"}]
            automation["action"] = [{"service": "persistent_notification.create", "data": {"message": f"Automation {alias} triggered"}}]
    
    automation["variables"] = {
        "auto_generated": True,
        "recommendation_type": automation_type,
        "confidence": confidence_value,
        "complexity": complexity,
        "ai_enhanced": ai_enhanced,
        "original_entities": entities,
        "original_description": description,
    }
    
    return automation


def _parse_ai_template(template: str, alias: str) -> dict | None:
    """Parse an AI-generated automation template."""
    import yaml
    import re
    
    if not template:
        return None
    
    clean_template = "\n".join([
        line for line in template.split('\n') 
        if not line.strip().startswith('#') and line.strip()
    ])
    
    template_content = clean_template
    if template_content.startswith('"""') and template_content.endswith('"""'):
        template_content = template_content[3:-3].strip()
    elif template_content.startswith("'''") and template_content.endswith("'''"):
        template_content = template_content[3:-3].strip()
    
    try:
        parsed = yaml.safe_load(template_content)
        if not isinstance(parsed, dict):
            _LOGGER.debug("Parsed template is not a dict for %s", alias)
            return None
        
        if 'automation' in parsed:
            parsed = parsed['automation']
        
        required_keys = ['alias', 'trigger', 'action']
        if all(key in parsed for key in required_keys):
            if 'condition' in parsed:
                fixed_conditions = _fix_conditions_list(parsed['condition'])
                if fixed_conditions:
                    parsed['condition'] = fixed_conditions
                else:
                    del parsed['condition']
            
            if "id" not in parsed:
                import uuid
                parsed["id"] = f"'{uuid.uuid4()}'"
            elif not (isinstance(parsed["id"], str) and parsed["id"].startswith("'") and parsed["id"].endswith("'")):
                import uuid
                parsed["id"] = f"'{uuid.uuid4()}'"
            return parsed
        else:
            missing = [k for k in required_keys if k not in parsed]
            _LOGGER.debug("Template missing keys for %s: %s", alias, missing)
            return None
            
    except yaml.YAMLError as e:
        _LOGGER.debug("YAML parse error for %s: %s", alias, e)
        return None
    except Exception as e:
        _LOGGER.debug("Unexpected error parsing template for %s: %s", alias, e)
        return None


def _fix_conditions_list(conditions: list) -> list:
    """Fix conditions list by ensuring each condition has 'condition' key instead of 'platform'."""
    if not isinstance(conditions, list):
        return []
    
    fixed = []
    for cond in conditions:
        if not isinstance(cond, dict):
            continue
            
        if "condition" in cond:
            fixed.append(_fix_condition_internal_keys(cond))
            continue
        
        if "platform" in cond:
            platform = cond.get("platform", "")
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
                fixed_cond = {"condition": condition_type}
                for key, value in cond.items():
                    if key != "platform":
                        fixed_cond[key] = value
                
                fixed_cond = _fix_condition_internal_keys(fixed_cond)
                fixed.append(fixed_cond)
                _LOGGER.debug("Fixed condition: platform=%s -> condition=%s", platform, condition_type)
    
    return fixed


def _fix_condition_internal_keys(condition: dict) -> dict:
    """Fix internal keys within a condition that AI often gets wrong."""
    if not isinstance(condition, dict):
        return condition
    
    fixed = dict(condition)
    
    if fixed.get("condition") == "time":
        if "weekdays" in fixed:
            fixed["weekday"] = fixed.pop("weekdays")
    
    return fixed


def _write_automations_file(file_path: Path, automations: list[dict]) -> None:
    """Write automations to YAML file (run in executor)."""
    import yaml
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
        yaml.dump(automations, f, allow_unicode=True, sort_keys=False, indent=2)