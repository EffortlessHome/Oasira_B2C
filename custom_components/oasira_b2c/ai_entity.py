"""Base entity for Oasira AI Conversation."""

from __future__ import annotations

from collections.abc import AsyncGenerator
import json
import logging
from typing import TYPE_CHECKING, Any

import httpx
import orjson
import voluptuous as vol

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigSubentry
from homeassistant.helpers import device_registry as dr, llm
from homeassistant.helpers.entity import Entity

from .ai_const import (
    CONF_BACKUP_MODEL,
    CONF_CHAT_MODEL,
    CONF_CONTEXT_THRESHOLD,
    CONF_CONTEXT_TRUNCATE_STRATEGY,
    CONF_MAX_FUNCTION_CALLS_PER_CONVERSATION,
    CONF_MAX_TOKENS,
    CONF_MODEL,
    CONF_NUM_CTX,
    CONF_SHORTEN_TOOL_CALL_ID,
    CONF_TEMPERATURE,
    CONF_TOP_P,
    DEFAULT_BACKUP_MODEL,
    DEFAULT_CHAT_MODEL,
    DEFAULT_CONTEXT_THRESHOLD,
    DEFAULT_CONTEXT_TRUNCATE_STRATEGY,
    DEFAULT_MAX_FUNCTION_CALLS_PER_CONVERSATION,
    DEFAULT_MAX_TOKENS,
    DEFAULT_NUM_CTX,
    DEFAULT_SHORTEN_TOOL_CALL_ID,
    DEFAULT_TEMPERATURE,
    DEFAULT_TOP_P,
    DOMAIN,
    get_model_config,
)
from .ai_exceptions import FunctionNotFound, ParseArgumentsFailed, TokenLengthExceededError
from .ai_helpers import OllamaClient

if TYPE_CHECKING:
    from . import OasiraAIConfigEntry

_LOGGER = logging.getLogger(__name__)

# Max number of back and forth with the LLM to generate a response
MAX_TOOL_ITERATIONS = 20


def _shorten_tool_call_id(tool_call_id: str) -> str:
    """Shorten tool call ID to exactly 9 alphanumeric characters as some providers require."""
    import hashlib

    return hashlib.sha256(tool_call_id.encode()).hexdigest()[:9]


def _format_structured_output(
    schema: vol.Schema, llm_api: llm.APIInstance | None
) -> dict[str, Any]:
    """Format the schema to be compatible with Ollama API."""
    # For Ollama, we use json schema format
    result: dict[str, Any] = {
        "type": "object",
        "properties": {},
    }
    
    # Convert voluptuous schema to JSON schema
    if hasattr(schema, 'schema'):
        # Handle basic schema structures
        schema_dict = schema.schema
        if isinstance(schema_dict, dict):
            for key, value in schema_dict.items():
                if isinstance(value, dict):
                    prop_type = value.get('type', 'string')
                    result["properties"][key] = {
                        "type": prop_type,
                        "description": value.get('description', ''),
                    }
    
    return result


def _convert_content_to_param(
    chat_content: list[conversation.Content],
    shorten_tool_call_id: bool = False,
) -> list[dict[str, Any]]:
    """Convert chat log content to Ollama message format."""
    messages: list[dict[str, Any]] = []

    for content in chat_content:
        if content.role == "system":
            messages.append({"role": "system", "content": content.content})
        elif content.role == "user":
            messages.append({"role": "user", "content": content.content})
        elif content.role == "assistant":
            msg: dict[str, Any] = {"role": "assistant"}
            if content.content:
                msg["content"] = content.content
            if content.tool_calls:
                # Ollama uses tool_calls with function calls
                msg["tool_calls"] = [
                    {
                        "id": _shorten_tool_call_id(tool_call.id)
                        if shorten_tool_call_id
                        else tool_call.id,
                        "type": "function",
                        "function": {
                            "name": tool_call.tool_name,
                            "arguments": json.dumps(tool_call.tool_args),
                        },
                    }
                    for tool_call in content.tool_calls
                ]
            messages.append(msg)
        elif content.role == "tool_result":
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": _shorten_tool_call_id(content.tool_call_id)
                    if shorten_tool_call_id
                    else content.tool_call_id,
                    "content": orjson.dumps(content.tool_result).decode(),
                }
            )

    return messages


class ExtendedOpenAIBaseLLMEntity(Entity):
    """Oasira AI base entity using Ollama."""

    _attr_has_entity_name = True
    _attr_name = None

    def __init__(
        self, entry: OasiraAIConfigEntry, subentry: ConfigSubentry
    ) -> None:
        """Initialize the entity."""
        self.entry = entry
        self.subentry = subentry
        self._attr_unique_id = subentry.subentry_id
        model = subentry.data.get(CONF_MODEL, subentry.data.get(CONF_CHAT_MODEL, DEFAULT_CHAT_MODEL))
        self._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, subentry.subentry_id)},
            name=subentry.title,
            manufacturer="Ollama",
            model=model,
            entry_type=dr.DeviceEntryType.SERVICE,
        )

    @property
    def _client(self) -> OllamaClient:
        """Return the Ollama client."""
        if hasattr(self.entry, "oasira_ai_runtime_data"):
            return self.entry.oasira_ai_runtime_data
        return self.entry.runtime_data

    async def _async_handle_chat_log(
        self,
        chat_log: conversation.ChatLog,
        function_tools: list[dict[str, Any]],
        exposed_entities: list[dict[str, Any]],
        llm_context: llm.LLMContext | None = None,
        structure_name: str | None = None,
        structure: vol.Schema | None = None,
    ) -> None:
        """Generate an answer for the chat log with streaming support."""
        options = self.subentry.data
        model = options.get(CONF_MODEL, options.get(CONF_CHAT_MODEL, DEFAULT_CHAT_MODEL))
        backup_model = options.get(CONF_BACKUP_MODEL, DEFAULT_BACKUP_MODEL)
        max_function_calls = options.get(
            CONF_MAX_FUNCTION_CALLS_PER_CONVERSATION,
            DEFAULT_MAX_FUNCTION_CALLS_PER_CONVERSATION,
        )
        shorten_tool_call_id = options.get(
            CONF_SHORTEN_TOOL_CALL_ID,
            DEFAULT_SHORTEN_TOOL_CALL_ID,
        )

        # Try primary model, fall back to backup model on failure
        last_error: Exception | None = None
        for attempt_model in [model, backup_model] if backup_model and backup_model != model else [model]:
            try:
                await self._async_handle_chat_log_with_model(
                    chat_log, function_tools, exposed_entities, llm_context,
                    attempt_model, max_function_calls, shorten_tool_call_id,
                    structure_name, structure
                )
                return  # Success, exit the method
            except Exception as err:
                last_error = err
                _LOGGER.warning(
                    "Model %s failed with error: %s. Attempting backup model %s if available.",
                    attempt_model, err, backup_model
                )
                # Re-raise if no backup model or this was the backup model
                if attempt_model == backup_model or not backup_model or backup_model == model:
                    raise
        
        # Should not reach here, but raise last error if we do
        if last_error:
            raise last_error

    async def _async_handle_chat_log_with_model(
        self,
        chat_log: conversation.ChatLog,
        function_tools: list[dict[str, Any]],
        exposed_entities: list[dict[str, Any]],
        llm_context: llm.LLMContext | None,
        model: str,
        max_function_calls: int,
        shorten_tool_call_id: bool,
        structure_name: str | None,
        structure: vol.Schema | None,
    ) -> None:
        """Generate an answer for the chat log with a specific model."""
        options = self.subentry.data

        # Get model-specific configuration
        model_config = get_model_config(model)

        messages = _convert_content_to_param(chat_log.content, shorten_tool_call_id)

        # Build API parameters for Ollama
        api_kwargs: dict[str, Any] = {
            "model": model,
        }

        # Add Ollama-specific options
        num_ctx = options.get(CONF_NUM_CTX, DEFAULT_NUM_CTX)
        if model_config.get("supports_num_ctx"):
            api_kwargs["num_ctx"] = num_ctx

        temperature = options.get(CONF_TEMPERATURE, DEFAULT_TEMPERATURE)
        if model_config.get("supports_temperature"):
            api_kwargs["temperature"] = temperature

        top_p = options.get(CONF_TOP_P, DEFAULT_TOP_P)
        if model_config.get("supports_top_p"):
            api_kwargs["top_p"] = top_p

        # Add tools if available (Ollama native tools support)
        tool_kwargs: dict[str, Any] = {}
        if function_tools:
            # Convert OpenAI-style tools to Ollama format
            ollama_tools = []
            for func_spec in function_tools:
                tool = {
                    "type": "function",
                    "function": {
                        "name": func_spec["spec"]["name"],
                        "description": func_spec["spec"].get("description", ""),
                        "parameters": func_spec["spec"].get("parameters", {}),
                    }
                }
                ollama_tools.append(tool)
            tool_kwargs["tools"] = ollama_tools

        # To prevent infinite loops, we limit the number of iterations
        for n_requests in range(MAX_TOOL_ITERATIONS):
            # Update tool_choice based on function call count
            # -1 means unlimited function calls
            if function_tools and 0 <= max_function_calls <= n_requests:
                # Disable tools for this iteration
                iteration_kwargs = {k: v for k, v in tool_kwargs.items() if k != "tools"}
            else:
                iteration_kwargs = tool_kwargs.copy()

            _LOGGER.info("Prompt for %s: %s", model, json.dumps(messages))

            # Call Ollama streaming API
            stream = await self._client.chat_stream(
                messages=messages,
                **api_kwargs,
                **iteration_kwargs,
            )

            # Process stream and collect tool calls
            pending_tool_calls: list[llm.ToolInput] = []
            full_response = ""

            async for content in chat_log.async_add_delta_content_stream(
                self.entity_id, self._transform_stream(chat_log, stream)
            ):
                if (
                    isinstance(content, conversation.AssistantContent)
                    and content.tool_calls
                ):
                    pending_tool_calls.extend(content.tool_calls)
                if isinstance(content, dict) and content.get("content"):
                    full_response += content["content"]

            if pending_tool_calls:
                _LOGGER.info("Response Tool Calls %s", pending_tool_calls)

            # Execute custom functions
            for tool_input in pending_tool_calls:
                function_tool = next(
                    (
                        f
                        for f in (function_tools)
                        if f["spec"]["name"] == tool_input.tool_name
                    ),
                    None,
                )

                if function_tool is None:
                    raise FunctionNotFound(tool_input.tool_name)

                tool_result_content = await self._execute_function_tool(
                    function_tool,
                    tool_input,
                    llm_context,
                    exposed_entities,
                )

                chat_log.async_add_assistant_content_without_tools(tool_result_content)

            # Update messages for next iteration
            messages = _convert_content_to_param(chat_log.content, shorten_tool_call_id)

            # Check if we need to continue (if there are pending tool results)
            if not chat_log.unresponded_tool_results:
                break

    async def _transform_stream(
        self,
        chat_log: conversation.ChatLog,
        result: httpx.Response,
    ) -> AsyncGenerator[
        conversation.AssistantContentDeltaDict | conversation.ToolResultContentDeltaDict
    ]:
        """Transform Ollama stream to Home Assistant format.
        
        Handles both Ollama native NDJSON format and OpenAI-compatible SSE format.
        """
        current_tool_calls: dict[int, dict[str, Any]] = {}
        first_chunk = True
        is_openai_format = False

        async for line in result.aiter_lines():
            if not line:
                continue
            
            # Check if this is SSE format (OpenAI-compatible)
            if line.startswith("data: "):
                is_openai_format = True
                line = line[6:]  # Remove "data: " prefix
            
            # Skip [DONE] signal in SSE format
            if line.strip() == "[DONE]":
                continue
            
            try:
                chunk = orjson.loads(line)
            except orjson.JSONDecodeError:
                continue

            _LOGGER.debug("Received chunk: %s", chunk)

            # Signal new assistant message on first chunk
            if first_chunk:
                yield {"role": "assistant"}
                first_chunk = False

            if is_openai_format:
                # OpenAI-compatible SSE format
                choices = chunk.get("choices", [])
                if not choices:
                    continue
                
                choice = choices[0]
                delta = choice.get("delta", {})
                
                # Handle content
                content = delta.get("content", "")
                if content:
                    yield {"content": content}

                # Handle tool calls in delta
                tool_calls = delta.get("tool_calls", [])
                for tool_call_data in tool_calls:
                    idx = tool_call_data.get("index", 0)
                    if idx not in current_tool_calls:
                        current_tool_calls[idx] = {
                            "id": tool_call_data.get("id", ""),
                            "name": "",
                            "arguments": "",
                        }

                    func = tool_call_data.get("function", {})
                    if func.get("name"):
                        current_tool_calls[idx]["name"] = func["name"]
                    if func.get("arguments"):
                        current_tool_calls[idx]["arguments"] += func["arguments"]

                # Check if this is the final chunk
                finish_reason = choice.get("finish_reason")
                if finish_reason == "tool_calls" and current_tool_calls:
                    for tc in self._build_tool_calls(current_tool_calls):
                        yield tc
                    current_tool_calls.clear()
                
                if finish_reason == "length":
                    raise TokenLengthExceededError(
                        self.subentry.data.get(CONF_MAX_TOKENS, DEFAULT_MAX_TOKENS)
                    )
            else:
                # Ollama native NDJSON format
                # Check for done signal
                if chunk.get("done"):
                    # Track usage if available
                    if "eval_count" in chunk or "prompt_eval_count" in chunk:
                        chat_log.async_trace(
                            {
                                "stats": {
                                    "input_tokens": chunk.get("prompt_eval_count", 0),
                                    "output_tokens": chunk.get("eval_count", 0),
                                }
                            }
                        )
                        # Check context threshold
                        total_tokens = chunk.get("prompt_eval_count", 0) + chunk.get("eval_count", 0)
                        if total_tokens > self.subentry.data.get(
                            CONF_CONTEXT_THRESHOLD, DEFAULT_CONTEXT_THRESHOLD
                        ):
                            await self._truncate_message_history(chat_log)
                    
                    # Emit accumulated tool calls
                    if current_tool_calls:
                        for tc in self._build_tool_calls(current_tool_calls):
                            yield tc
                        current_tool_calls.clear()
                    continue

                # Handle message content
                message = chunk.get("message", {})
                content = message.get("content", "")
                
                if content:
                    yield {"content": content}

                # Handle tool calls
                tool_calls = chunk.get("tool_calls") or []
                for tool_call_data in tool_calls:
                    idx = tool_call_data.get("index", 0)
                    if idx not in current_tool_calls:
                        current_tool_calls[idx] = {
                            "id": tool_call_data.get("id", ""),
                            "name": "",
                            "arguments": "",
                        }

                    func = tool_call_data.get("function", {})
                    if func.get("name"):
                        current_tool_calls[idx]["name"] = func["name"]
                    if func.get("arguments"):
                        current_tool_calls[idx]["arguments"] += func["arguments"]

                # Handle stop reason
                if chunk.get("done_reason") == "length":
                    raise TokenLengthExceededError(
                        self.subentry.data.get(CONF_MAX_TOKENS, DEFAULT_MAX_TOKENS)
                    )
                
                if chunk.get("done_reason") == "tool_calls" and current_tool_calls:
                    for tc in self._build_tool_calls(current_tool_calls):
                        yield tc
                    current_tool_calls.clear()

    def _build_tool_calls(
        self, current_tool_calls: dict[int, dict[str, Any]]
    ) -> list[conversation.ToolResultContentDeltaDict]:
        """Build tool calls from accumulated data."""
        tool_calls_list = []
        for idx in sorted(current_tool_calls.keys()):
            tool_call = current_tool_calls[idx]
            try:
                args = json.loads(tool_call["arguments"])
            except json.JSONDecodeError as err:
                raise ParseArgumentsFailed(tool_call["arguments"]) from err
            tool_calls_list.append(
                {
                    "tool_calls": [
                        llm.ToolInput(
                            id=tool_call["id"],
                            tool_name=tool_call["name"],
                            tool_args=args,
                            external=True,
                        )
                    ]
                }
            )
        return tool_calls_list

    async def _execute_function_tool(
        self,
        function_tool: dict[str, Any],
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext | None,
        exposed_entities: list[dict[str, Any]],
    ) -> conversation.ToolResultContent:
        """Execute a custom function."""
        from .ai_functions import get_function

        arguments: dict[str, Any] = tool_input.tool_args
        function_config = function_tool["function"]
        function = get_function(function_config["type"])

        if self.should_run_in_background(arguments):
            # create a delayed function and execute in background
            function_config = self.get_delayed_function_config(
                function_config, arguments
            )
            function = get_function(function_config["type"])
            self.entry.async_create_task(
                self.hass,
                function.execute(
                    self.hass,
                    function_config,
                    arguments,
                    llm_context,
                    exposed_entities,
                    self._client,
                ),
            )
            result = "Scheduled"
        else:
            result = await function.execute(
                self.hass, function_config, arguments, llm_context, exposed_entities, self._client
            )

        return conversation.ToolResultContent(
            agent_id=self.entity_id,
            tool_call_id=tool_input.id,
            tool_name=tool_input.tool_name,
            tool_result={"result": str(result)},
        )

    def should_run_in_background(self, arguments: dict[str, Any]) -> bool:
        """Check if function needs delay."""
        return isinstance(arguments, dict) and arguments.get("delay") is not None

    def get_delayed_function_config(
        self, function_config: dict[str, Any], arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute function with delay."""
        # create a composite function with delay in script function
        return {
            "type": "composite",
            "sequence": [
                {
                    "type": "script",
                    "sequence": [{"delay": arguments["delay"]}],
                },
                function_config,
            ],
        }

    async def _truncate_message_history(self, chat_log: conversation.ChatLog) -> None:
        """Truncate message history based on strategy."""
        options = self.subentry.data
        strategy = options.get(
            CONF_CONTEXT_TRUNCATE_STRATEGY, DEFAULT_CONTEXT_TRUNCATE_STRATEGY
        )

        if strategy == "clear":
            # Keep only system prompt and last user message
            # This is handled by refreshing the LLM data
            _LOGGER.info("Context threshold exceeded, conversation history cleared")
            last_user_message_index = None
            messages = chat_log.content
            for i in reversed(range(len(messages))):
                if isinstance(messages[i], conversation.UserContent):
                    last_user_message_index = i
                    break

            if last_user_message_index is not None:
                del messages[1:last_user_message_index]