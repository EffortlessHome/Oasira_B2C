"""AI Task integration for Oasira AI Conversation."""

from __future__ import annotations

from json import JSONDecodeError
import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from homeassistant.components import ai_task, conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util.json import json_loads

from .ai_const import (
    CONF_FUNCTION_TOOLS,
    CONF_CHAT_MODEL,
    CONF_MAX_TOKENS,
    CONF_MODEL,
    CONF_TEMPERATURE,
    CONF_TOP_P,
    DEFAULT_AI_TASK_NAME,
    DEFAULT_AI_TASK_OPTIONS,
    DEFAULT_CHAT_MODEL,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    DEFAULT_TEMPERATURE,
    DEFAULT_TOP_P,
    get_model_config,
)
from .ai_entity import ExtendedOpenAIBaseLLMEntity

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry

    from . import OasiraAIConfigEntry

_LOGGER = logging.getLogger(__name__)

OasiraAIConfigEntry = ConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up AI Task entities."""
    _LOGGER.debug("Setting up merged AI task platform for entry %s", config_entry.entry_id)

    subentries = [
        subentry
        for subentry in config_entry.subentries.values()
        if subentry.subentry_type == "ai_task_data"
    ]

    if not subentries:
        subentries = [
            SimpleNamespace(
                subentry_type="ai_task_data",
                subentry_id=f"{config_entry.entry_id}_ai_task",
                title=DEFAULT_AI_TASK_NAME,
                data=DEFAULT_AI_TASK_OPTIONS | {CONF_FUNCTION_TOOLS: []},
            )
        ]

    for subentry in subentries:
        if subentry.subentry_type != "ai_task_data":
            continue

        entity = ExtendedOpenAITaskEntity(config_entry, subentry)
        if subentry.subentry_id in config_entry.subentries:
            async_add_entities([entity], config_subentry_id=subentry.subentry_id)
        else:
            async_add_entities([entity])


class ExtendedOpenAITaskEntity(
    ai_task.AITaskEntity,
    ExtendedOpenAIBaseLLMEntity,
):
    """Oasira AI Task entity."""

    def __init__(
        self, entry: OasiraAIConfigEntry, subentry: ConfigSubentry
    ) -> None:
        """Initialize the entity."""
        super().__init__(entry, subentry)
        # Ollama doesn't support structured image generation via API
        # Only GENERATE_DATA is supported for text generation
        self._attr_supported_features = (
            ai_task.AITaskEntityFeature.GENERATE_DATA
            | ai_task.AITaskEntityFeature.SUPPORT_ATTACHMENTS
        )

    async def _async_generate_data(
        self,
        task: ai_task.GenDataTask,
        chat_log: conversation.ChatLog,
    ) -> ai_task.GenDataTaskResult:
        """Handle a generate data task."""
        # Call _async_handle_chat_log with empty custom_functions and exposed_entities
        # AI Task operates without functions
        await self._async_handle_chat_log(
            chat_log,
            function_tools=[],
            exposed_entities=[],
            llm_context=None,
            structure_name=task.name,
            structure=task.structure,
        )

        # Extract response
        if not isinstance(chat_log.content[-1], conversation.AssistantContent):
            raise HomeAssistantError(
                "Last content in chat log is not an AssistantContent"
            )

        text = chat_log.content[-1].content or ""

        # Handle structured output
        if not task.structure:
            return ai_task.GenDataTaskResult(
                conversation_id=chat_log.conversation_id,
                data=text,
            )

        try:
            data = json_loads(text)
        except JSONDecodeError as err:
            _LOGGER.error(
                "Failed to parse JSON response: %s. Response: %s",
                err,
                text,
            )
            raise HomeAssistantError("Error with structured response") from err

        return ai_task.GenDataTaskResult(
            conversation_id=chat_log.conversation_id,
            data=data,
        )

    async def _async_generate_text(
        self,
        task: ai_task.GenTextTask,
        chat_log: conversation.ChatLog,
    ) -> ai_task.GenTextTaskResult:
        """Handle a text generation task."""
        # For text generation, we can use the chat completion API
        
        # Build the prompt
        prompt = task.prompt
        if task.context:
            prompt = f"{task.context}\n\n{prompt}"
        
        # Get model configuration
        options = self.subentry.data
        model = options.get(CONF_MODEL, options.get(CONF_CHAT_MODEL, DEFAULT_MODEL))
        
        # Get Ollama-specific parameters
        client = self._client
        
        try:
            # Call Ollama API
            response = await client.chat(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                stream=False,
            )
            
            # Extract the generated text
            generated_text = response.get("message", {}).get("content", "")
            
            return ai_task.GenTextTaskResult(
                conversation_id=chat_log.conversation_id,
                text=generated_text,
            )
            
        except Exception as err:
            _LOGGER.error("Failed to generate text: %s", err)
            raise HomeAssistantError(f"Failed to generate text: {err}") from err