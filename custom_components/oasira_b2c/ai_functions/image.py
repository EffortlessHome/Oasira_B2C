"""Image analysis functions for Oasira AI Conversation using Ollama."""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant

from .base import Function


class ImageAnalysisFunction(Function):
    """Function for analyzing images using Ollama vision models."""

    def __init__(self) -> None:
        """Initialize the image analysis function."""
        super().__init__()

    async def execute(
        self,
        hass: HomeAssistant,
        function_config: dict[str, Any],
        arguments: dict[str, Any],
        llm_context: Any,
        exposed_entities: list[dict[str, Any]],
        client: Any,
    ) -> dict[str, Any]:
        """Execute the image analysis function using Ollama."""
        try:
            # Extract parameters
            image_url = arguments["image_url"]
            prompt = arguments.get("prompt", "Please describe this image in detail.")
            model = arguments.get("model", "llava")

            # Convert image URL to base64 if local
            image_content = await self._get_image_content(hass, image_url)

            # Create messages for Ollama vision model
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{image_content}"},
                        },
                    ],
                }
            ]

            # Call Ollama chat API
            response = await client.chat(
                model=model,
                messages=messages,
                stream=False,
                timeout=300.0,
            )

            # Extract the analysis from Ollama response
            analysis = response.get("message", {}).get("content", "")

            return {
                "success": True,
                "content": analysis,
                "data": {"image_url": image_url, "analysis": analysis, "model": model},
            }

        except Exception as err:
            return {
                "success": False,
                "content": f"Failed to analyze image: {err}",
            }

    async def _get_image_content(self, hass: HomeAssistant, image_url: str) -> str:
        """Get image content as base64 encoded string."""
        from urllib.parse import urlparse

        # Check if it's a local file
        parsed = urlparse(image_url)
        
        if parsed.scheme in ("http", "https"):
            # Download from URL
            import httpx
            async with httpx.AsyncClient() as http_client:
                response = await http_client.get(image_url)
                response.raise_for_status()
                return base64.b64encode(response.content).decode()
        else:
            # Local file
            if not hass.config.is_allowed_path(image_url):
                raise ValueError(f"Cannot access path: {image_url}")
            
            if not Path(image_url).exists():
                raise ValueError(f"File not found: {image_url}")
            
            with open(image_url, "rb") as f:
                return base64.b64encode(f.read()).decode()
