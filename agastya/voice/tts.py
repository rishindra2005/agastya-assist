"""Sarvam text-to-speech implementation."""

import asyncio
import base64
import httpx

from agastya.config import get_settings
from agastya.voice.types import SarvamError
from agastya.voice.wav import concat_wavs, split_text


class SarvamTTS:
    """Sarvam AI Text-to-Speech client."""

    def __init__(
        self,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
        model: str = "bulbul:v3",
        speaker: str = "shubh",
        max_chars: int | None = None,
    ):
        self.api_key = api_key
        self.client = client
        self.model = model
        self.speaker = speaker
        self.max_chars = max_chars if max_chars is not None else (1500 if "v2" in model else 2500)
        self.endpoint = "https://api.sarvam.ai/text-to-speech"
        self.retry_delay = 1.0

    async def synthesize(self, text: str, language_code: str) -> bytes:
        """Convert text into speech WAV bytes.

        Texts longer than the per-request limit are split on sentence boundaries
        and synthesized sequentially, then concatenated into a single valid WAV.
        """
        api_key = self.api_key or get_settings().SARVAM_API_KEY
        if not api_key:
            raise SarvamError("SARVAM_API_KEY not set")

        text = text.strip()
        if not text:
            return b""

        chunks = split_text(text, self.max_chars)
        if not chunks:
            return b""

        should_close_client = False
        client = self.client
        if client is None:
            client = httpx.AsyncClient(timeout=60.0)
            should_close_client = True

        headers = {
            "api-subscription-key": api_key,
            "Content-Type": "application/json",
        }

        wav_parts: list[bytes] = []

        try:
            for chunk in chunks:
                payload = {
                    "text": chunk,
                    "language_code": language_code,
                    "model": self.model,
                    "speaker": self.speaker,
                }

                try:
                    resp = await client.post(self.endpoint, json=payload, headers=headers)
                except httpx.RequestError as exc:
                    raise SarvamError(f"Sarvam HTTP request failed: {exc}") from exc

                # Retry once on 429 or 5xx
                if resp.status_code == 429 or 500 <= resp.status_code < 600:
                    await asyncio.sleep(self.retry_delay)
                    try:
                        resp = await client.post(self.endpoint, json=payload, headers=headers)
                    except httpx.RequestError as exc:
                        raise SarvamError(f"Sarvam HTTP retry failed: {exc}") from exc

                if resp.is_error:
                    msg = f"Sarvam API error ({resp.status_code}): {resp.text[:300]}"
                    raise SarvamError(msg, status_code=resp.status_code)

                try:
                    data = resp.json()
                except Exception as exc:
                    raise SarvamError(f"Failed to parse Sarvam TTS JSON response: {exc}") from exc

                audios = data.get("audios")
                if not audios or not isinstance(audios, list):
                    raise SarvamError("Sarvam TTS response missing 'audios' list")

                for audio_b64 in audios:
                    try:
                        decoded = base64.b64decode(audio_b64)
                    except Exception as exc:
                        raise SarvamError(f"Failed to base64-decode audio: {exc}") from exc
                    wav_parts.append(decoded)
        finally:
            if should_close_client:
                await client.aclose()

        return concat_wavs(wav_parts)
