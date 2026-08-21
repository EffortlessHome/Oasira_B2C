"""Speech-to-text support for Oasira Home."""

from __future__ import annotations

from collections.abc import AsyncIterable
import logging

import httpx

from homeassistant.components.stt import (
    AudioBitRates,
    AudioCodecs,
    AudioFormats,
    SpeechMetadata,
    SpeechResult,
    SpeechResultState,
    SpeechToTextEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.httpx_client import get_async_client

from .ai_const import DEFAULT_CONF_BASE_URL
from .const import DOMAIN, NAME

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Oasira speech-to-text entity."""
    async_add_entities([OasiraSTTEntity(hass, config_entry)])


class OasiraSTTEntity(SpeechToTextEntity):
    """Transcribe audio using the Oasira Cloudflare AI agent."""

    _attr_name = f"{NAME} STT"
    _attr_unique_id = "oasira_stt"

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        """Initialize the speech-to-text entity."""
        self.hass = hass
        self._attr_unique_id = f"{config_entry.entry_id}_stt"

    @property
    def device_info(self):
        """Return the Oasira device information."""
        return {
            "identifiers": {(DOMAIN, NAME)},
            "name": NAME,
            "manufacturer": NAME,
        }

    @property
    def supported_languages(self) -> list[str]:
        """Return languages accepted by the speech recognition service."""
        return ["en"]

    @property
    def supported_formats(self) -> list[AudioFormats]:
        """Return supported audio formats."""
        return [AudioFormats.WAV]

    @property
    def supported_codecs(self) -> list[AudioCodecs]:
        """Return supported audio codecs."""
        return [AudioCodecs.PCM]

    @property
    def supported_bit_rates(self) -> list[AudioBitRates]:
        """Return supported audio bitrates."""
        return [AudioBitRates.BITRATE_16]

    @property
    def supported_sample_rates(self) -> list[int]:
        """Return supported sample rates."""
        return [16000]

    @property
    def supported_channels(self) -> list[int]:
        """Return supported channel counts."""
        return [1]

    async def async_process_audio_stream(
        self, metadata: SpeechMetadata, stream: AsyncIterable[bytes]
    ) -> SpeechResult:
        """Transcribe an audio stream through the Oasira agent."""
        audio_data = b"".join([chunk async for chunk in stream])
        if not audio_data:
            return SpeechResult(None, SpeechResultState.ERROR)

        try:
            client = get_async_client(self.hass)
            response = await client.post(
                f"{DEFAULT_CONF_BASE_URL.rstrip('/')}/v1/audio/transcriptions",
                files={
                    "file": (
                        "audio.wav",
                        audio_data,
                        "audio/wav",
                    )
                },
                data={"language": metadata.language},
                timeout=httpx.Timeout(120.0),
            )
            response.raise_for_status()
            result = response.json()
            text = str(result.get("text", "")).strip()
        except (httpx.HTTPError, ValueError, TypeError) as err:
            _LOGGER.error("Oasira speech recognition request failed: %s", err)
            return SpeechResult(None, SpeechResultState.ERROR)

        if not text:
            return SpeechResult(None, SpeechResultState.ERROR)
        return SpeechResult(text, SpeechResultState.SUCCESS)