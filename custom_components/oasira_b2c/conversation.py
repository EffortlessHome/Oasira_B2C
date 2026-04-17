"""Compatibility wrapper for the conversation platform."""

from __future__ import annotations

from .ai_conversation import ExtendedOpenAIAgentEntity, async_setup_entry

__all__ = ["async_setup_entry", "ExtendedOpenAIAgentEntity"]