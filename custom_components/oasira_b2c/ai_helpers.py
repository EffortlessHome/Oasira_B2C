"""Helper functions for Oasira AI Conversation component."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

import httpx

from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_should_expose
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.helpers.template import Template
from homeassistant.exceptions import HomeAssistantError

from .ai_const import (
    DEFAULT_CONF_BASE_URL,
    DEFAULT_MODEL,
    get_model_config,
)

_LOGGER = logging.getLogger(__name__)


def get_exposed_entities(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Get exposed entities."""
    states = [
        state
        for state in hass.states.async_all()
        if async_should_expose(hass, conversation.DOMAIN, state.entity_id)
    ]
    entity_registry = er.async_get(hass)
    exposed_entities = []
    for state in states:
        entity_id = state.entity_id
        entity = entity_registry.async_get(entity_id)

        aliases: list[str] = []
        if entity and entity.aliases:
            aliases = list(entity.aliases)

        exposed_entities.append(
            {
                "entity_id": entity_id,
                "name": state.name,
                "state": state.state,
                "aliases": aliases,
            }
        )
    return exposed_entities


def convert_to_template(
    settings: Any,
    template_keys: list[str] | None = None,
    hass: HomeAssistant | None = None,
) -> None:
    if template_keys is None:
        template_keys = ["data", "event_data", "target", "service"]
    _convert_to_template(settings, template_keys, hass, [])


def _convert_to_template(
    settings: Any,
    template_keys: list[str],
    hass: HomeAssistant | None,
    parents: list[str],
) -> None:
    if isinstance(settings, dict):
        for key, value in settings.items():
            if isinstance(value, str) and (
                key in template_keys or set(parents).intersection(template_keys)
            ):
                settings[key] = Template(value, hass)
            if isinstance(value, dict):
                parents.append(key)
                _convert_to_template(value, template_keys, hass, parents)
                parents.pop()
            if isinstance(value, list):
                parents.append(key)
                for item in value:
                    _convert_to_template(item, template_keys, hass, parents)
                parents.pop()
    if isinstance(settings, list):
        for setting in settings:
            _convert_to_template(setting, template_keys, hass, parents)


class OpenAICompatibleClient:
    """Client for the OpenAI-compatible Oasira agent API."""

    def __init__(
        self,
        hass: HomeAssistant,
        timeout: float = 120.0,
    ) -> None:
        """Initialize the OpenAI-compatible client."""
        self.hass = hass
        self.base_url = DEFAULT_CONF_BASE_URL
        self.timeout = timeout

    def _api_url(self, path: str) -> str:
        """Build a URL below the OpenAI-compatible API root."""
        api_root = self.base_url if self.base_url.endswith("/v1") else f"{self.base_url}/v1"
        return f"{api_root}/{path.lstrip('/')}"

    def _api_url_with_systemid(self, path: str) -> str:
        """Build an API URL with the required system identifier."""
        return f"{self._api_url(path)}?systemid={self._systemid()}"

    def _systemid(self) -> str:
        """Return the configured system identifier for API requests."""
        systemid = self.hass.data.get("oasira_b2c", {}).get("systemid")
        if not isinstance(systemid, str) or not systemid:
            raise HomeAssistantError(
                "The Oasira systemid is required for the AI request"
            )
        if not re.fullmatch(r"[a-f0-9]{32}", systemid, re.IGNORECASE):
            raise HomeAssistantError(
                "The Oasira systemid must be a 32-character hexadecimal identifier"
            )
        return systemid

    async def list_models(self) -> list[dict[str, Any]]:
        """List models exposed by the OpenAI-compatible API."""
        # Use Home Assistant's async client to avoid SSL certificate issues
        client = get_async_client(self.hass)
        response = await client.get(
            self._api_url_with_systemid("models"),
            timeout=httpx.Timeout(self.timeout),
        )
        response.raise_for_status()
        data = response.json()
        return [{"name": model["id"]} for model in data.get("data", [])]

    async def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        stream: bool = True,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> dict[str, Any] | dict:
        """Send a chat request to the OpenAI-compatible API.
        
        Args:
            model: The model name to use
            messages: List of message dictionaries with 'role' and 'content'
            stream: Whether to stream the response
            timeout: Request timeout in seconds (defaults to client timeout)
            **kwargs: Additional OpenAI parameters (temperature, top_p, etc.)
            
        Returns:
            Chat response dictionary
        """
        return await self._chat_openai_compat(
            model=model,
            messages=messages,
            stream=stream,
            timeout=timeout,
            **kwargs,
        )

    async def _chat_openai_compat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        stream: bool,
        timeout: float | None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Send chat request using OpenAI-compatible endpoint."""
        client = get_async_client(self.hass)

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": stream,
        }

        if "temperature" in kwargs and kwargs["temperature"] is not None:
            payload["temperature"] = kwargs["temperature"]
        if "top_p" in kwargs and kwargs["top_p"] is not None:
            payload["top_p"] = kwargs["top_p"]
        if "max_tokens" in kwargs and kwargs["max_tokens"] is not None:
            payload["max_tokens"] = kwargs["max_tokens"]
        if "stop" in kwargs and kwargs["stop"] is not None:
            payload["stop"] = kwargs["stop"]
        if "tools" in kwargs and kwargs["tools"] is not None:
            payload["tools"] = kwargs["tools"]
        if "tool_choice" in kwargs and kwargs["tool_choice"] is not None:
            payload["tool_choice"] = kwargs["tool_choice"]

        request_timeout = timeout if timeout is not None else self.timeout
        response = await client.post(
            self._api_url_with_systemid("chat/completions"),
            json=payload,
            timeout=httpx.Timeout(request_timeout),
        )
        response.raise_for_status()
        return self._convert_to_message_response(response.json())

    async def chat_stream(
        self,
        model: str,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> httpx.Response:
        """Send a streaming chat request to the OpenAI-compatible API.
        
        Args:
            model: The model name to use
            messages: List of message dictionaries with 'role' and 'content'
            **kwargs: Additional OpenAI parameters
            
        Returns:
            HTTP response with streaming data
        """
        return await self._chat_stream_openai_compat(model, messages, **kwargs)

    async def _chat_stream_openai_compat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> httpx.Response:
        """Send streaming chat request using OpenAI-compatible API."""
        # Use Home Assistant's async client to avoid SSL certificate issues
        client = get_async_client(self.hass)
        
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        
        # Map kwargs to OpenAI-compatible format
        if "temperature" in kwargs and kwargs["temperature"] is not None:
            payload["temperature"] = kwargs["temperature"]
        if "top_p" in kwargs and kwargs["top_p"] is not None:
            payload["top_p"] = kwargs["top_p"]
        if "max_tokens" in kwargs and kwargs["max_tokens"] is not None:
            payload["max_tokens"] = kwargs["max_tokens"]
        if "stop" in kwargs and kwargs["stop"] is not None:
            payload["stop"] = kwargs["stop"]
        if "tools" in kwargs and kwargs["tools"] is not None:
            payload["tools"] = kwargs["tools"]
        if "tool_choice" in kwargs and kwargs["tool_choice"] is not None:
            payload["tool_choice"] = kwargs["tool_choice"]
        
        response = await client.post(
            self._api_url_with_systemid("chat/completions"),
            json=payload,
            timeout=httpx.Timeout(self.timeout),
        )
        response.raise_for_status()
        return response

    async def generate(
        self,
        model: str,
        prompt: str,
        stream: bool = True,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Generate text through the chat completion endpoint.
        
        Args:
            model: The model name to use
            prompt: The prompt text
            stream: Whether to stream the response
            **kwargs: Additional OpenAI parameters
            
        Returns:
            Generation response dictionary
        """
        return await self.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            stream=stream,
            **kwargs,
        )

    async def generate_stream(
        self,
        model: str,
        prompt: str,
        **kwargs: Any,
    ) -> httpx.Response:
        """Stream generated text through the chat completion endpoint.
        
        Args:
            model: The model name to use
            prompt: The prompt text
            **kwargs: Additional OpenAI parameters
            
        Returns:
            HTTP response with streaming data
        """
        return await self._chat_stream_openai_compat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            **kwargs,
        )

    def _convert_to_message_response(self, openai_response: dict[str, Any]) -> dict[str, Any]:
        """Adapt an OpenAI response for existing integration call sites."""
        try:
            # Extract from OpenAI format
            choices = openai_response.get("choices", [])
            if not choices:
                return {"message": {"content": ""}}
            
            choice = choices[0]
            message = choice.get("message", {})
            content = message.get("content", "")
            
            return {
                "message": {
                    "role": message.get("role", "assistant"),
                    "content": content,
                },
                "done": choice.get("finish_reason") is not None,
            }
        except Exception:
            return {"message": {"content": str(openai_response)}}

    async def check_connection(self) -> tuple[bool, str]:
        """Check if the OpenAI-compatible API is accessible.
        
        Returns:
            Tuple of (success, message)
        """
        try:
            # Use Home Assistant's async client to avoid SSL certificate issues
            client = get_async_client(self.hass)
            
            response = await client.get(
                self._api_url_with_systemid("models"),
                timeout=httpx.Timeout(self.timeout),
            )
            if response.status_code == 200:
                return True, "Connected to Oasira agent"

            # Older agent deployments may not expose model discovery. The
            # health endpoint still proves that the configured service is reachable.
            if response.status_code == 404:
                health_response = await client.get(
                    f"{self.base_url[:-3].rstrip('/') if self.base_url.endswith('/v1') else self.base_url}/health?systemid={self._systemid()}",
                    timeout=httpx.Timeout(self.timeout),
                )
                if health_response.status_code == 200:
                    return True, "Connected to Oasira agent (model discovery unavailable)"

            return False, (
                f"Unexpected status code {response.status_code} from "
                f"{self._api_url('models')}"
            )
        except httpx.ConnectError:
            return False, f"Could not connect to Oasira agent at {self.base_url}"
        except httpx.TimeoutException:
            return False, "Connection to Oasira agent timed out"
        except Exception as e:
            return False, f"Error connecting to Oasira agent: {str(e)}"


async def get_authenticated_client(
    hass: HomeAssistant,
    timeout: float = 120.0,
) -> OpenAICompatibleClient:
    """Create and validate an Oasira agent client.
    
    Args:
        hass: Home Assistant instance
        timeout: Request timeout in seconds
        
    Returns:
        OpenAICompatibleClient instance
        
    Raises:
        httpx.ConnectError: If cannot connect to the Oasira agent
        httpx.HTTPStatusError: If the agent returns an error
    """
    client = OpenAICompatibleClient(hass=hass, timeout=timeout)
    
    # Validate connection by listing models
    success, message = await client.check_connection()
    if not success:
        if "ConnectError" in message:
            raise httpx.ConnectError(message)
        elif "timed out" in message:
            raise httpx.TimeoutException(message)
        else:
            raise httpx.HTTPStatusError(
                message,
                request=httpx.Request("GET", DEFAULT_CONF_BASE_URL),
                response=httpx.Response(500),
            )
    
    return client


