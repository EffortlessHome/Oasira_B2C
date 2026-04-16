"""Config flow for Ollama Conversation integration."""

from __future__ import annotations

import logging
import types
from typing import Any

import httpx
import voluptuous as vol
import yaml

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TemplateSelector,
    TextSelector,
)

from .ai_const import (
    CONF_ADVANCED_OPTIONS,
    CONF_BACKUP_MODEL,
    CONF_BASE_URL,
    CONF_CHAT_MODEL,
    CONF_CONTEXT_THRESHOLD,
    CONF_CONTEXT_TRUNCATE_STRATEGY,
    CONF_FUNCTION_TOOLS,
    CONF_MAX_FUNCTION_CALLS_PER_CONVERSATION,
    CONF_MAX_TOKENS,
    CONF_MODEL,
    CONF_NUM_CTX,
    CONF_PROMPT,
    CONF_SHORTEN_TOOL_CALL_ID,
    CONF_SKILLS,
    CONF_TEMPERATURE,
    CONF_TIMEOUT,
    CONF_TOP_P,
    CONTEXT_TRUNCATE_STRATEGIES,
    DEFAULT_ADVANCED_OPTIONS,
    DEFAULT_AI_TASK_NAME,
    DEFAULT_AI_TASK_OPTIONS,
    DEFAULT_BACKUP_MODEL,
    DEFAULT_CHAT_MODEL,
    DEFAULT_CONF_BASE_URL,
    DEFAULT_CONF_FUNCTION_TOOLS,
    DEFAULT_CONTEXT_THRESHOLD,
    DEFAULT_CONTEXT_TRUNCATE_STRATEGY,
    DEFAULT_CONVERSATION_NAME,
    DEFAULT_MAX_FUNCTION_CALLS_PER_CONVERSATION,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    DEFAULT_NAME,
    DEFAULT_NUM_CTX,
    DEFAULT_PROMPT,
    DEFAULT_SHORTEN_TOOL_CALL_ID,
    DEFAULT_TEMPERATURE,
    DEFAULT_TIMEOUT,
    DEFAULT_TOP_P,
    DOMAIN,
)

# Store for available models during config flow
CONFIG_FLOW_MODELS: dict[str, list[str]] = {}
from .ai_helpers import OllamaClient, get_authenticated_client
from .ai_skills import SkillManager

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_NAME, default="Ollama Chat"): str,
        vol.Optional(CONF_BASE_URL, default=DEFAULT_CONF_BASE_URL): str,
    }
)

STEP_MODEL_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_MODEL): SelectSelector(
            SelectSelectorConfig(
                options=[],
                mode=SelectSelectorMode.DROPDOWN,
            )
        ),
    }
)

DEFAULT_CONF_FUNCTION_TOOLS_STR = yaml.dump(
    DEFAULT_CONF_FUNCTION_TOOLS, sort_keys=False
)

DEFAULT_OPTIONS = types.MappingProxyType(
    {
        CONF_PROMPT: DEFAULT_PROMPT,
        CONF_MODEL: DEFAULT_MODEL,
        CONF_CHAT_MODEL: DEFAULT_CHAT_MODEL,
        CONF_MAX_TOKENS: DEFAULT_MAX_TOKENS,
        CONF_MAX_FUNCTION_CALLS_PER_CONVERSATION: DEFAULT_MAX_FUNCTION_CALLS_PER_CONVERSATION,
        CONF_TOP_P: DEFAULT_TOP_P,
        CONF_TEMPERATURE: DEFAULT_TEMPERATURE,
        CONF_FUNCTION_TOOLS: DEFAULT_CONF_FUNCTION_TOOLS_STR,
        CONF_CONTEXT_THRESHOLD: DEFAULT_CONTEXT_THRESHOLD,
        CONF_CONTEXT_TRUNCATE_STRATEGY: DEFAULT_CONTEXT_TRUNCATE_STRATEGY,
        CONF_SHORTEN_TOOL_CALL_ID: DEFAULT_SHORTEN_TOOL_CALL_ID,
        CONF_ADVANCED_OPTIONS: DEFAULT_ADVANCED_OPTIONS,
        CONF_NUM_CTX: DEFAULT_NUM_CTX,
        CONF_TIMEOUT: DEFAULT_TIMEOUT,
    }
)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> OllamaClient:
    """Validate the user input allows us to connect.

    Data has the keys from STEP_USER_DATA_SCHEMA with values provided by the user.
    """
    base_url = data.get(CONF_BASE_URL, DEFAULT_CONF_BASE_URL)

    try:
        client = await get_authenticated_client(
            hass=hass,
            base_url=base_url,
        )
        return client
    except httpx.ConnectError as err:
        raise HomeAssistantError(
            f"Could not connect to Ollama at {base_url}. "
            "Please make sure Ollama is running and accessible."
        ) from err
    except httpx.TimeoutException as err:
        raise HomeAssistantError(
            f"Connection to Ollama at {base_url} timed out. "
            "Please check if Ollama is responding."
        ) from err
    except Exception as err:
        raise HomeAssistantError(
            f"Error connecting to Ollama: {err}"
        ) from err


async def get_available_models(hass: HomeAssistant, base_url: str) -> list[str]:
    """Get list of available models from Ollama."""
    try:
        client = OllamaClient(base_url=base_url, timeout=30.0)
        models = await client.list_models()
        # Extract model names from the response
        model_names = []
        for model in models:
            if isinstance(model, dict):
                # Handle both formats: {"name": "..."} or {"model": "..."}
                name = model.get("name") or model.get("model") or model.get("id", "")
                if name:
                    model_names.append(name)
            elif isinstance(model, str):
                model_names.append(model)
        
        _LOGGER.debug("Found %d models on Ollama at %s", len(model_names), base_url)
        return model_names
    except Exception as err:
        _LOGGER.warning("Failed to get models from Ollama: %s", err)
        return []


class ExtendedOpenAIConversationConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Ollama Conversation."""

    VERSION = 3

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        if user_input is None:
            return self.async_show_form(
                step_id="user", data_schema=STEP_USER_DATA_SCHEMA
            )

        errors = {}

        try:
            await validate_input(self.hass, user_input)
        except HomeAssistantError as err:
            errors["base"] = str(err)
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Unexpected exception")
            errors["base"] = "unknown"
        else:
            # Store base URL and name for next step
            self._base_url = user_input.get(CONF_BASE_URL, DEFAULT_CONF_BASE_URL)
            self._name = user_input.get(CONF_NAME, DEFAULT_NAME)
            
            # Fetch available models
            models = await get_available_models(self.hass, self._base_url)
            CONFIG_FLOW_MODELS[self.flow_id] = models
            
            if models:
                # Show model selection step
                return await self.async_step_select_model()
            else:
                # No models found, proceed with default
                _LOGGER.warning("No models found on Ollama, using default model")
                return self._create_entry_with_default_model()

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    async def async_step_select_model(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle model selection step."""
        models = CONFIG_FLOW_MODELS.get(self.flow_id, [])
        
        if not models:
            return self._create_entry_with_default_model()
        
        if user_input is not None:
            selected_model = user_input.get(CONF_MODEL)
            
            # Build conversation options with selected model
            # Set both CONF_MODEL and CONF_CHAT_MODEL so both schema fields show the selected model
            conversation_options = dict(DEFAULT_OPTIONS)
            conversation_options[CONF_MODEL] = selected_model
            conversation_options[CONF_CHAT_MODEL] = selected_model
            
            # Build AI task options with selected model
            ai_task_options = dict(DEFAULT_AI_TASK_OPTIONS)
            ai_task_options[CONF_MODEL] = selected_model
            
            return self.async_create_entry(
                title=self._name,
                data={
                    CONF_NAME: self._name,
                    CONF_BASE_URL: self._base_url,
                    CONF_MODEL: selected_model,
                },
                subentries=[
                    {
                        "subentry_type": "conversation",
                        "data": conversation_options,
                        "title": DEFAULT_CONVERSATION_NAME,
                        "unique_id": None,
                    },
                    {
                        "subentry_type": "ai_task_data",
                        "data": ai_task_options,
                        "title": DEFAULT_AI_TASK_NAME,
                        "unique_id": None,
                    },
                ],
            )
        
        # Create schema with available models
        schema = vol.Schema({
            vol.Required(CONF_MODEL): SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(value=model, label=model)
                        for model in models
                    ],
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
        })
        
        return self.async_show_form(
            step_id="select_model",
            data_schema=schema,
            description_placeholders={
                "base_url": self._base_url,
                "model_count": str(len(models)),
            }
        )

    def _create_entry_with_default_model(self) -> ConfigFlowResult:
        """Create entry with default model when no models are available."""
        selected_model = DEFAULT_MODEL
        
        # Build conversation options with selected model
        # Set both CONF_MODEL and CONF_CHAT_MODEL so both schema fields show the selected model
        conversation_options = dict(DEFAULT_OPTIONS)
        conversation_options[CONF_MODEL] = selected_model
        conversation_options[CONF_CHAT_MODEL] = selected_model
        
        # Build AI task options with selected model
        ai_task_options = dict(DEFAULT_AI_TASK_OPTIONS)
        ai_task_options[CONF_MODEL] = selected_model
        
        return self.async_create_entry(
            title=self._name,
            data={
                CONF_NAME: self._name,
                CONF_BASE_URL: self._base_url,
                CONF_MODEL: selected_model,
            },
            subentries=[
                {
                    "subentry_type": "conversation",
                    "data": conversation_options,
                    "title": DEFAULT_CONVERSATION_NAME,
                    "unique_id": None,
                },
                {
                    "subentry_type": "ai_task_data",
                    "data": ai_task_options,
                    "title": DEFAULT_AI_TASK_NAME,
                    "unique_id": None,
                },
            ],
        )

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return subentries supported by this integration."""
        return {
            "conversation": ExtendedOpenAISubentryFlowHandler,
            "ai_task_data": ExtendedOpenAIAITaskSubentryFlowHandler,
        }


class ExtendedOpenAISubentryFlowHandler(ConfigSubentryFlow):
    """Flow for managing Ollama Conversation subentries."""

    options: dict[str, Any]
    _temp_data: dict[str, Any] | None = None
    _available_skills: list[dict[str, Any]] | None = None
    _available_models: list[str] = []

    @property
    def _is_new(self) -> bool:
        """Return if this is a new subentry."""
        return self.source == "user"

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a subentry."""
        self.options = dict(DEFAULT_OPTIONS)
        return await self.async_step_init()

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Handle reconfiguration of a subentry."""
        self.options = dict(self._get_reconfigure_subentry().data)
        return await self.async_step_init()

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Manage the options."""
        # abort if entry is not loaded
        if self._get_entry().state != ConfigEntryState.LOADED:
            return self.async_abort(reason="entry_not_loaded")

        # Load available skills
        if self._available_skills is None:
            self._available_skills = await self._async_get_skills()

        if user_input is not None:
            # Check if advanced options is enabled
            if user_input.get(CONF_ADVANCED_OPTIONS, False):
                # Store data and move to advanced step
                self._temp_data = user_input
                return await self.async_step_advanced()

            # No advanced options, save directly
            if self._is_new:
                title = user_input.get(CONF_NAME, DEFAULT_NAME)
                if CONF_NAME in user_input:
                    del user_input[CONF_NAME]
                return self.async_create_entry(
                    title=title,
                    data=user_input,
                )
            return self.async_update_and_abort(
                self._get_entry(),
                self._get_reconfigure_subentry(),
                data=user_input,
            )

        schema = self.openai_config_option_schema(self.options, self._available_skills)

        if self._is_new:
            schema = {
                vol.Optional(CONF_NAME, default=DEFAULT_NAME): str,
                **schema,
            }

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(schema), self.options
            ),
        )

    async def async_step_advanced(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Handle advanced options step."""
        if user_input is not None:
            # Merge advanced options with temp data
            final_data = {**(self._temp_data or {}), **user_input}

            if self._is_new:
                title = final_data.get(CONF_NAME, DEFAULT_NAME)
                final_data.pop(CONF_NAME, None)
                return self.async_create_entry(
                    title=title,
                    data=final_data,
                )
            return self.async_update_and_abort(
                self._get_entry(),
                self._get_reconfigure_subentry(),
                data=final_data,
            )

        schema: dict[Any, Any] = {}

        # Add temperature option
        schema[
            vol.Optional(
                CONF_TEMPERATURE,
                default=DEFAULT_TEMPERATURE,
            )
        ] = NumberSelector(NumberSelectorConfig(min=0, max=2, step=0.05))

        # Add top_p option
        schema[
            vol.Optional(
                CONF_TOP_P,
                default=DEFAULT_TOP_P,
            )
        ] = NumberSelector(NumberSelectorConfig(min=0, max=1, step=0.05))

        # Add num_ctx option
        schema[
            vol.Optional(
                CONF_NUM_CTX,
                default=DEFAULT_NUM_CTX,
            )
        ] = NumberSelector(NumberSelectorConfig(min=512, max=131072, step=512))

        # Add shorten_tool_call_id option
        schema[
            vol.Optional(
                CONF_SHORTEN_TOOL_CALL_ID,
                default=DEFAULT_SHORTEN_TOOL_CALL_ID,
            )
        ] = BooleanSelector()

        return self.async_show_form(
            step_id="advanced",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(schema), self.options
            ),
        )

    async def _async_get_skills(self) -> list[dict[str, Any]]:
        """Load available skills using SkillManager."""
        skill_manager = await SkillManager.async_get_instance(self.hass)
        return [
            {
                "name": skill.name,
                "description": skill.description,
            }
            for skill in skill_manager.get_all_skills()
        ]

    def openai_config_option_schema(
        self, options: dict[str, Any], skills: list[dict[str, Any]] | None = None
    ) -> dict:
        """Return a schema for Ollama completion options."""
        # If creating a new subentry and no skills in options, default to all loaded skills
        default_skills: list[str] = []
        if self._is_new and CONF_SKILLS not in options and skills:
            default_skills = [skill["name"] for skill in skills]

        current_skills = options.get(CONF_SKILLS, default_skills)

        schema: dict = {
            vol.Optional(
                CONF_PROMPT,
                default=DEFAULT_PROMPT,
            ): TemplateSelector(),
            vol.Optional(
                CONF_MODEL,
                default=DEFAULT_MODEL,
            ): TextSelector(),
            vol.Optional(
                CONF_BACKUP_MODEL,
                default=DEFAULT_BACKUP_MODEL,
            ): TextSelector(),
            vol.Optional(
                CONF_CHAT_MODEL,
                default=DEFAULT_CHAT_MODEL,
            ): TextSelector(),
            vol.Optional(
                CONF_MAX_TOKENS,
                default=DEFAULT_MAX_TOKENS,
            ): int,
            vol.Optional(
                CONF_MAX_FUNCTION_CALLS_PER_CONVERSATION,
                default=DEFAULT_MAX_FUNCTION_CALLS_PER_CONVERSATION,
            ): int,
            vol.Optional(CONF_SKILLS, default=current_skills): SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(
                            value=skill["name"],
                            label=skill["name"],
                        )
                        for skill in (skills or [])
                    ],
                    mode=SelectSelectorMode.DROPDOWN,
                    multiple=True,
                )
            ),
            vol.Optional(
                CONF_FUNCTION_TOOLS,
                default=DEFAULT_CONF_FUNCTION_TOOLS_STR,
            ): TemplateSelector(),
            vol.Optional(
                CONF_CONTEXT_THRESHOLD,
                default=DEFAULT_CONTEXT_THRESHOLD,
            ): int,
            vol.Optional(
                CONF_CONTEXT_TRUNCATE_STRATEGY,
                default=DEFAULT_CONTEXT_TRUNCATE_STRATEGY,
            ): SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(value=strategy["key"], label=strategy["label"])
                        for strategy in CONTEXT_TRUNCATE_STRATEGIES
                    ],
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(
                CONF_ADVANCED_OPTIONS,
                default=DEFAULT_ADVANCED_OPTIONS,
            ): BooleanSelector(),
        }

        # Remove skills field if no skills available
        if not skills:
            schema = {
                key: value
                for key, value in schema.items()
                if not (isinstance(key, vol.Optional) and key.schema == CONF_SKILLS)
            }

        return schema


class ExtendedOpenAIAITaskSubentryFlowHandler(ConfigSubentryFlow):
    """Flow for managing AI Task subentries."""

    options: dict[str, Any]
    _temp_data: dict[str, Any] | None = None

    @property
    def _is_new(self) -> bool:
        """Return if this is a new subentry."""
        return self.source == "user"

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a subentry."""
        self.options = dict(DEFAULT_AI_TASK_OPTIONS)
        return await self.async_step_init()

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Handle reconfiguration of a subentry."""
        self.options = dict(self._get_reconfigure_subentry().data)
        return await self.async_step_init()

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Manage the options."""
        # Abort if entry is not loaded
        if self._get_entry().state != ConfigEntryState.LOADED:
            return self.async_abort(reason="entry_not_loaded")

        if user_input is not None:
            # Check if advanced options is enabled
            if user_input.get(CONF_ADVANCED_OPTIONS, False):
                # Store data and move to advanced step
                self._temp_data = user_input
                return await self.async_step_advanced()

            # No advanced options, save directly
            if self._is_new:
                title = user_input.get(CONF_NAME, DEFAULT_AI_TASK_NAME)
                if CONF_NAME in user_input:
                    del user_input[CONF_NAME]
                return self.async_create_entry(
                    title=title,
                    data=user_input,
                )
            return self.async_update_and_abort(
                self._get_entry(),
                self._get_reconfigure_subentry(),
                data=user_input,
            )

        schema: dict = {}

        if self._is_new:
            schema[vol.Optional(CONF_NAME, default=DEFAULT_AI_TASK_NAME)] = str

        schema.update(
            {
                vol.Optional(
                    CONF_MODEL,
                    default=DEFAULT_MODEL,
                ): TextSelector(),
                vol.Optional(
                    CONF_CHAT_MODEL,
                    default=DEFAULT_CHAT_MODEL,
                ): TextSelector(),
                vol.Optional(
                    CONF_MAX_TOKENS,
                    default=DEFAULT_MAX_TOKENS,
                ): int,
                vol.Optional(
                    CONF_ADVANCED_OPTIONS,
                    default=DEFAULT_ADVANCED_OPTIONS,
                ): BooleanSelector(),
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(schema), self.options
            ),
        )

    async def async_step_advanced(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Handle advanced options step."""
        if user_input is not None:
            # Merge advanced options with temp data
            final_data = {**(self._temp_data or {}), **user_input}

            if self._is_new:
                title = final_data.get(CONF_NAME, DEFAULT_AI_TASK_NAME)
                final_data.pop(CONF_NAME, None)
                return self.async_create_entry(
                    title=title,
                    data=final_data,
                )
            return self.async_update_and_abort(
                self._get_entry(),
                self._get_reconfigure_subentry(),
                data=final_data,
            )

        schema: dict[Any, Any] = {}

        # Add temperature option
        schema[
            vol.Optional(
                CONF_TEMPERATURE,
                default=DEFAULT_TEMPERATURE,
            )
        ] = NumberSelector(NumberSelectorConfig(min=0, max=2, step=0.05))

        # Add top_p option
        schema[
            vol.Optional(
                CONF_TOP_P,
                default=DEFAULT_TOP_P,
            )
        ] = NumberSelector(NumberSelectorConfig(min=0, max=1, step=0.05))

        # Add num_ctx option
        schema[
            vol.Optional(
                CONF_NUM_CTX,
                default=DEFAULT_NUM_CTX,
            )
        ] = NumberSelector(NumberSelectorConfig(min=512, max=131072, step=512))

        # Add shorten_tool_call_id option
        schema[
            vol.Optional(
                CONF_SHORTEN_TOOL_CALL_ID,
                default=DEFAULT_SHORTEN_TOOL_CALL_ID,
            )
        ] = BooleanSelector()

        return self.async_show_form(
            step_id="advanced",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(schema), self.options
            ),
        )