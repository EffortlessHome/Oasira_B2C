"""Object detection functions for Oasira AI Conversation using Ollama vision models."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant

from .base import Function


class DetectObjectsFunction(Function):
    """Function for detecting objects (people, animals, packages, vehicles) in images."""

    DEFAULT_PROMPT = """Analyze this image and identify the following object categories:
- people: Any human beings present
- animals: Any animals including pets (dogs, cats, birds, etc.) and wildlife
- packages: Any parcels, boxes, deliveries, or mail items
- vehicles: Any cars, trucks, motorcycles, bicycles, or other vehicles

For each category found, provide:
1. Whether any objects of that type are present (yes/no)
2. A brief description if helpful
3. An approximate count if visible

Format your response as a structured analysis."""

    async def execute(
        self,
        hass: HomeAssistant,
        function_config: dict[str, Any],
        arguments: dict[str, Any],
        llm_context: Any,
        exposed_entities: list[dict[str, Any]],
        client: Any,
    ) -> dict[str, Any]:
        """Execute object detection using Ollama vision model."""
        try:
            # Extract parameters
            image_url = arguments.get("image_url")
            categories = arguments.get("categories", ["people", "animals", "packages", "vehicles"])
            model = arguments.get("model", "llava")
            prompt = arguments.get("prompt", self.DEFAULT_PROMPT)

            # Build category-specific prompt
            category_list = ", ".join(categories) if categories else "people, animals, packages, vehicles"
            focused_prompt = f"""{prompt}

Focus specifically on detecting: {category_list}

Provide a clear, concise response for each category."""

            # Get image content as base64
            image_content = await self._get_image_content(hass, image_url)

            # Create messages for Ollama vision model
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": focused_prompt},
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
            )

            # Extract the analysis from Ollama response
            analysis = response.get("message", {}).get("content", "")

            # Parse structured results
            structured_results = self._parse_detection_results(analysis, categories)

            # Auto-create timeline event if people detected and camera_entity_id provided
            camera_entity_id = arguments.get("camera_entity_id")
            if camera_entity_id and structured_results.get("people", {}).get("detected"):
                await self._create_timeline_event(
                    hass=hass,
                    camera_entity_id=camera_entity_id,
                    detection_results=structured_results,
                    image_content=image_content,
                )

            return {
                "success": True,
                "content": analysis,
                "data": {
                    "image_url": image_url,
                    "analysis": analysis,
                    "model": model,
                    "detections": structured_results,
                },
            }

        except Exception as err:
            return {
                "success": False,
                "content": f"Failed to detect objects: {err}",
            }

    async def _create_timeline_event(
        self,
        hass: HomeAssistant,
        camera_entity_id: str,
        detection_results: dict[str, dict[str, Any]],
        image_content: str,
    ) -> None:
        """Create a timeline event when people are detected."""
        try:
            from ..timeline_event import get_timeline_manager
            from ..timeline_service import get_area_for_camera

            camera_state = hass.states.get(camera_entity_id)
            camera_name = camera_state.name if camera_state else camera_entity_id

            # Get area info if available
            area_info = {}
            if hasattr(hass, "areas"):
                area_registry = hass.areas
                # Find area from device or entity context
                pass  # Could be enhanced with device registry lookup

            # Decode image content for snapshot
            snapshot_data = None
            if image_content:
                import base64
                try:
                    snapshot_data = base64.b64decode(image_content)
                except Exception:
                    pass

            manager = await get_timeline_manager(hass)

            # Determine labels from detection
            labels = []
            for category, result in detection_results.items():
                if result.get("detected"):
                    labels.append(category.rstrip("s"))  # "people" -> "person"

            # Create the timeline event
            event = await manager.create_person_detection_event(
                camera_entity_id=camera_entity_id,
                camera_name=camera_name,
                snapshot_data=snapshot_data,
                area_id=area_info.get("area_id"),
                area_name=area_info.get("area_name"),
                confidence=detection_results.get("people", {}).get("confidence", 1.0),
                labels=labels if labels else ["person"],
                description=f"Detected: {', '.join(labels) if labels else 'person'}",
            )

            _LOGGER.info(
                "Created timeline event %s for camera %s - detected: %s",
                event.event_id, camera_name, labels
            )

        except Exception as e:
            _LOGGER.error("Failed to create timeline event: %s", e)

    def _parse_detection_results(
        self, analysis: str, categories: list[str]
    ) -> dict[str, dict[str, Any]]:
        """Parse the AI response into structured detection results."""
        results = {}
        analysis_lower = analysis.lower()

        category_keywords = {
            "people": ["person", "people", "human", "man", "woman", "child", "boy", "girl"],
            "animals": ["animal", "dog", "cat", "bird", "pet", "wildlife", "horse", "cow", "deer"],
            "packages": ["package", "parcel", "box", "delivery", "mail", "envelope", "package"],
            "vehicles": ["car", "truck", "motorcycle", "bicycle", "vehicle", "van", "bus", "suv"],
        }

        for category in categories:
            keywords = category_keywords.get(category, [category])
            found = any(keyword in analysis_lower for keyword in keywords)
            results[category] = {
                "detected": found,
                "present": "yes" if found else "no",
            }

        return results

    async def _get_image_content(self, hass: HomeAssistant, image_url: str) -> str:
        """Get image content as base64 encoded string."""
        from urllib.parse import urlparse

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

