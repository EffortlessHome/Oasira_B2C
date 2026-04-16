"""Helper functions for Oasira AI Conversation component."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx

from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_should_expose
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.helpers.template import Template

from .const import (
    CONF_BASE_URL,
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


class OllamaClient:
    """Ollama API client for interacting with local Ollama instances."""

    def __init__(
        self,
        hass: HomeAssistant,
        base_url: str = DEFAULT_CONF_BASE_URL,
        timeout: float = 120.0,
    ) -> None:
        """Initialize the Ollama client."""
        self.hass = hass
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._use_compat_api = False  # Will be set based on API version detection

    async def list_models(self) -> list[dict[str, Any]]:
        """List available models from Ollama."""
        # Use Home Assistant's async client to avoid SSL certificate issues
        client = get_async_client(self.hass)
        
        # Try new endpoint first, fall back to legacy
        try:
            response = await client.get(f"{self.base_url}/api/tags")
            response.raise_for_status()
            data = response.json()
            return data.get("models", [])
        except httpx.HTTPStatusError:
            # Fall back to OpenAI-compatible models endpoint
            response = await client.get(f"{self.base_url}/v1/models")
            response.raise_for_status()
            data = response.json()
            return [{"name": m["id"]} for m in data.get("data", [])]

    async def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        stream: bool = True,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> dict[str, Any] | dict:
        """Send a chat request to Ollama.
        
        Args:
            model: The model name to use
            messages: List of message dictionaries with 'role' and 'content'
            stream: Whether to stream the response
            timeout: Request timeout in seconds (defaults to client timeout)
            **kwargs: Additional Ollama parameters (temperature, top_p, etc.)
            
        Returns:
            Chat response dictionary
        """
        # Use Home Assistant's async client to avoid SSL certificate issues
        client = get_async_client(self.hass)
        
        # Try OpenAI-compatible API first (Ollama v0.5+)
        try:
            payload: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "stream": stream,  # Respect the stream parameter
            }
            
            # Map kwargs to OpenAI-compatible format
            if "temperature" in kwargs and kwargs["temperature"] is not None:
                payload["temperature"] = kwargs["temperature"]
            if "top_p" in kwargs and kwargs["top_p"] is not None:
                payload["top_p"] = kwargs["top_p"]
            if "max_tokens" in kwargs and kwargs["max_tokens"] is not None:
                payload["max_tokens"] = kwargs["max_tokens"]
            if "num_ctx" in kwargs and kwargs["num_ctx"] is not None:
                payload["num_ctx"] = kwargs["num_ctx"]
            if "stop" in kwargs and kwargs["stop"] is not None:
                payload["stop"] = kwargs["stop"]
            
            # Use provided timeout or fall back to client's default timeout
            request_timeout = timeout if timeout is not None else self.timeout
            response = await client.post(
                f"{self.base_url}/v1/chat/completions",
                json=payload,
                timeout=httpx.Timeout(request_timeout),
            )
            response.raise_for_status()
            
            # Convert OpenAI format back to Ollama format for compatibility
            result = response.json()
            return self._convert_to_ollama_format(result)
            
        except httpx.HTTPStatusError:
            # Fall back to legacy Ollama API
            payload: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "stream": stream,
            }
            
            # Add Ollama-specific options
            ollama_options = {}
            for key, value in kwargs.items():
                if value is not None and value != 0:
                    ollama_options[key] = value
            
            if ollama_options:
                payload["options"] = ollama_options
            
            # Use provided timeout or fall back to client's default timeout
            request_timeout = timeout if timeout is not None else self.timeout
            response = await client.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=httpx.Timeout(request_timeout),
            )
            response.raise_for_status()
            return response.json()

    async def chat_stream(
        self,
        model: str,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> httpx.Response:
        """Send a streaming chat request to Ollama.
        
        Args:
            model: The model name to use
            messages: List of message dictionaries with 'role' and 'content'
            **kwargs: Additional Ollama parameters
            
        Returns:
            HTTP response with streaming data
        """
        # Try OpenAI-compatible API first (Ollama v0.5+)
        try:
            return await self._chat_stream_openai_compat(model, messages, **kwargs)
        except httpx.HTTPStatusError:
            # Fall back to legacy Ollama API
            return await self._chat_stream_legacy(model, messages, **kwargs)

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
        if "num_ctx" in kwargs and kwargs["num_ctx"] is not None:
            payload["num_ctx"] = kwargs["num_ctx"]
        if "stop" in kwargs and kwargs["stop"] is not None:
            payload["stop"] = kwargs["stop"]
        
        response = await client.post(
            f"{self.base_url}/v1/chat/completions",
            json=payload,
            timeout=httpx.Timeout(self.timeout),
        )
        response.raise_for_status()
        return response

    async def _chat_stream_legacy(
        self,
        model: str,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> httpx.Response:
        """Send streaming chat request using legacy Ollama API."""
        # Use Home Assistant's async client to avoid SSL certificate issues
        client = get_async_client(self.hass)
        
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        
        # Add Ollama-specific options
        ollama_options = {}
        for key, value in kwargs.items():
            if value is not None and value != 0:
                ollama_options[key] = value
        
        if ollama_options:
            payload["options"] = ollama_options
        
        response = await client.post(
            f"{self.base_url}/api/chat",
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
        """Send a generate request to Ollama (non-chat completion).
        
        Args:
            model: The model name to use
            prompt: The prompt text
            stream: Whether to stream the response
            **kwargs: Additional Ollama parameters
            
        Returns:
            Generation response dictionary
        """
        # Use Home Assistant's async client to avoid SSL certificate issues
        client = get_async_client(self.hass)
        
        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "stream": stream,
        }
        
        # Add Ollama-specific options
        ollama_options = {}
        for key, value in kwargs.items():
            if value is not None and value != 0:
                ollama_options[key] = value
        
        if ollama_options:
            payload["options"] = ollama_options
        
        response = await client.post(
            f"{self.base_url}/api/generate",
            json=payload,
            timeout=httpx.Timeout(self.timeout),
        )
        response.raise_for_status()
        return response.json()

    async def generate_stream(
        self,
        model: str,
        prompt: str,
        **kwargs: Any,
    ) -> httpx.Response:
        """Send a streaming generate request to Ollama.
        
        Args:
            model: The model name to use
            prompt: The prompt text
            **kwargs: Additional Ollama parameters
            
        Returns:
            HTTP response with streaming data
        """
        # Use Home Assistant's async client to avoid SSL certificate issues
        client = get_async_client(self.hass)
        
        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "stream": True,
        }
        
        # Add Ollama-specific options
        ollama_options = {}
        for key, value in kwargs.items():
            if value is not None and value != 0:
                ollama_options[key] = value
        
        if ollama_options:
            payload["options"] = ollama_options
        
        response = await client.post(
            f"{self.base_url}/api/generate",
            json=payload,
            timeout=httpx.Timeout(self.timeout),
        )
        response.raise_for_status()
        return response

    def _convert_to_ollama_format(self, openai_response: dict[str, Any]) -> dict[str, Any]:
        """Convert OpenAI API response format to Ollama format for compatibility."""
        try:
            # Extract from OpenAI format
            choices = openai_response.get("choices", [])
            if not choices:
                return {"message": {"content": ""}}
            
            choice = choices[0]
            message = choice.get("message", {})
            content = message.get("content", "")
            
            # Convert back to Ollama format
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
        """Check if Ollama is accessible.
        
        Returns:
            Tuple of (success, message)
        """
        try:
            # Use Home Assistant's async client to avoid SSL certificate issues
            client = get_async_client(self.hass)
            
            # Try OpenAI-compatible endpoint first
            try:
                response = await client.get(f"{self.base_url}/v1/models")
                if response.status_code == 200:
                    self._use_compat_api = True
                    return True, "Connected to Ollama (OpenAI-compatible API)"
            except httpx.HTTPStatusError:
                pass
            
            # Fall back to legacy endpoint
            response = await client.get(f"{self.base_url}/api/tags")
            if response.status_code == 200:
                self._use_compat_api = False
                return True, "Connected to Ollama (Legacy API)"
            return False, f"Unexpected status code: {response.status_code}"
        except httpx.ConnectError:
            return False, f"Could not connect to Ollama at {self.base_url}"
        except httpx.TimeoutException:
            return False, "Connection to Ollama timed out"
        except Exception as e:
            return False, f"Error connecting to Ollama: {str(e)}"


async def get_authenticated_client(
    hass: HomeAssistant,
    base_url: str | None = None,
    timeout: float = 120.0,
) -> OllamaClient:
    """Create and validate an Ollama client.
    
    Args:
        hass: Home Assistant instance
        base_url: Ollama base URL (defaults to localhost:11434)
        timeout: Request timeout in seconds
        
    Returns:
        OllamaClient instance
        
    Raises:
        httpx.ConnectError: If cannot connect to Ollama
        httpx.HTTPStatusError: If Ollama returns an error
    """
    url = base_url or DEFAULT_CONF_BASE_URL
    client = OllamaClient(hass=hass, base_url=url, timeout=timeout)
    
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
                request=httpx.Request("GET", url),
                response=httpx.Response(500),
            )
    
    return client
