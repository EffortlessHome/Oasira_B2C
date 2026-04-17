"""Template tool for Jinja2 rendering."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, llm

from .base import Function

_LOGGER = logging.getLogger(__name__)


class TemplateFunction(Function):
    def __init__(self) -> None:
        """Initialize template tool."""
        super().__init__(
            vol.Schema(
                {
                    vol.Required("value_template"): cv.template,
                    vol.Optional("parse_result"): bool,
                }
            )
        )

    def validate_schema(self, function_config: dict[str, Any]) -> dict[str, Any]:
        """Validate and convert function configuration using the schema."""
        # Make a copy to avoid modifying the original
        config = dict(function_config)
        
        # Handle value_template that might be a dict or Template object instead of string
        value_tpl = config.get("value_template")
        _LOGGER.debug("value_template type: %s, value: %s", type(value_tpl), repr(value_tpl)[:200] if value_tpl else None)
        
        if not isinstance(value_tpl, str):
            if hasattr(value_tpl, 'template'):
                # It's a Template object - extract the string
                config['value_template'] = value_tpl.template
                _LOGGER.debug("Converted Template object to string: %s", config['value_template'][:100])
            elif isinstance(value_tpl, dict):
                _LOGGER.error("value_template is a dict: %s", value_tpl)
                # Try to extract template string from dict
                if 'template' in value_tpl:
                    config['value_template'] = value_tpl['template']
                    _LOGGER.debug("Extracted template from dict")
                elif 'value_template' in value_tpl:
                    config['value_template'] = value_tpl['value_template']
                    _LOGGER.debug("Extracted value_template from dict")
                else:
                    raise InvalidFunction("template") from ValueError(f"Cannot interpret value_template dict: {value_tpl}")
        
        try:
            return super().validate_schema(config)
        except vol.Error as e:
            _LOGGER.error("Template validation error: %s", e)
            raise InvalidFunction("template") from e

    async def execute(
        self,
        hass: HomeAssistant,
        function_config: dict[str, Any],
        arguments: dict[str, Any],
        llm_context: llm.LLMContext | None,
        exposed_entities: list[dict[str, Any]],
        client: Any = None,
    ) -> Any:
        return function_config["value_template"].async_render(
            arguments,
            parse_result=function_config.get("parse_result", False),
        )

