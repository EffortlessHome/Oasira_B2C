"""Oasira AI Conversation agent entity."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any, Literal

import httpx

from homeassistant.components import conversation
from homeassistant.components.conversation import (
    ChatLog,
    ConversationEntity,
    ConversationEntityFeature,
    ConversationInput,
    ConversationResult,
    async_get_chat_log,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import intent, llm, template
from homeassistant.helpers.chat_session import async_get_chat_session
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .ai_const import (
    CONF_PROMPT,
    DEFAULT_CONVERSATION_NAME,
    DEFAULT_PROMPT,
    DOMAIN,
    EVENT_CONVERSATION_FINISHED,
)
from .ai_entity import ExtendedOpenAIBaseLLMEntity
from .ai_helpers import get_exposed_entities

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Ollama Conversation entities."""
    _LOGGER.debug("Setting up merged AI conversation platform for entry %s", config_entry.entry_id)

    subentries = [
        subentry
        for subentry in config_entry.subentries.values()
        if subentry.subentry_type == "conversation"
    ]

    if not subentries:
        subentries = [
            SimpleNamespace(
                subentry_type="conversation",
                subentry_id=f"{config_entry.entry_id}_conversation",
                title=DEFAULT_CONVERSATION_NAME,
                data={
                    CONF_PROMPT: DEFAULT_PROMPT,
                },
            )
        ]

    for subentry in subentries:
        if subentry.subentry_type != "conversation":
            continue

        entity = ExtendedOpenAIAgentEntity(config_entry, subentry)
        if subentry.subentry_id in config_entry.subentries:
            async_add_entities([entity], config_subentry_id=subentry.subentry_id)
        else:
            async_add_entities([entity])


class ExtendedOpenAIAgentEntity(
    ConversationEntity,
    conversation.AbstractConversationAgent,
    ExtendedOpenAIBaseLLMEntity,
):
    """Oasira AI conversation agent."""

    _attr_supports_streaming = True
    _attr_supported_features = ConversationEntityFeature.CONTROL

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        """Return a list of supported languages."""
        return MATCH_ALL

    async def async_added_to_hass(self) -> None:
        """When entity is added to Home Assistant."""
        await super().async_added_to_hass()
        conversation.async_set_agent(self.hass, self.entry, self)

    async def async_will_remove_from_hass(self) -> None:
        """When entity will be removed from Home Assistant."""
        conversation.async_unset_agent(self.hass, self.entry)
        await super().async_will_remove_from_hass()

    async def async_process(self, user_input: ConversationInput) -> ConversationResult:
        """Process a sentence."""
        with (
            async_get_chat_session(self.hass, user_input.conversation_id) as session,
            async_get_chat_log(self.hass, session, user_input) as chat_log,
        ):
            return await self._async_handle_message(user_input, chat_log)

    async def _async_handle_message(
        self,
        user_input: ConversationInput,
        chat_log: ChatLog,
    ) -> ConversationResult:
        """Call the API."""
        # Create LLM context
        llm_context = user_input.as_llm_context(DOMAIN)

        # Get exposed entities for function tools
        exposed_entities = self._get_exposed_entities()

        # Build custom prompt with exposed entities
        system_prompt = self._build_system_prompt(
            exposed_entities, llm_context, user_input
        )

        # Set system prompt in chat log
        chat_log.content[0] = conversation.SystemContent(content=system_prompt)

        # Call the LLM

        try:
            await self._async_handle_chat_log(
                chat_log,
                function_tools=[],
                exposed_entities=exposed_entities,
                llm_context=llm_context,
            )
        except httpx.HTTPError as err:
            _LOGGER.error(err)
            intent_response = intent.IntentResponse(language=user_input.language)
            intent_response.async_set_error(
                intent.IntentResponseErrorCode.UNKNOWN,
                f"Sorry, I had a problem talking to Ollama: {err}",
            )
            return conversation.ConversationResult(
                response=intent_response, conversation_id=user_input.conversation_id
            )
        except HomeAssistantError as err:
            _LOGGER.error("Error during conversation: %s", err, exc_info=True)
            intent_response = intent.IntentResponse(language=user_input.language)
            intent_response.async_set_error(
                intent.IntentResponseErrorCode.UNKNOWN,
                f"Something went wrong: {err}",
            )
            return conversation.ConversationResult(
                response=intent_response, conversation_id=user_input.conversation_id
            )

        # Fire conversation finished event
        self.hass.bus.async_fire(
            EVENT_CONVERSATION_FINISHED,
            {
                "user_input": user_input,
                "messages": [c.as_dict() for c in chat_log.content],
                "agent_id": self.subentry.subentry_id,
            },
        )

        # Build response from chat log
        intent_response = intent.IntentResponse(language=user_input.language)

        # Get last assistant message
        last_content = chat_log.content[-1]
        if isinstance(last_content, conversation.AssistantContent):
            intent_response.async_set_speech(last_content.content or "")
        else:
            intent_response.async_set_speech("")

        return ConversationResult(
            response=intent_response,
            conversation_id=chat_log.conversation_id,
            continue_conversation=chat_log.continue_conversation,
        )

    def _build_system_prompt(
        self,
        exposed_entities: list[dict],
        llm_context: llm.LLMContext,
        user_input: ConversationInput,
    ) -> str:
        """Build system prompt with exposed entities."""
        raw_prompt: str = self.subentry.data.get(CONF_PROMPT, DEFAULT_PROMPT)

        result = template.Template(raw_prompt, self.hass).async_render(
            {
                "ha_name": self.hass.config.location_name,
                "exposed_entities": exposed_entities,
                "current_device_id": llm_context.device_id,
                "user_input": user_input,
            },
            parse_result=False,
        )

        return str(result)

    def _get_exposed_entities(self) -> list[dict[str, Any]]:
        return get_exposed_entities(self.hass)
