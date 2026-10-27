"""Sarvam Speech-To-Text client implementation."""

import asyncio
import mimetypes

import httpx

from agastya.config import get_settings
from agastya.voice.types import SarvamError, Transcript

SARVAM_STT_URL = "https://api.sarvam.ai/speech-to-text"


class SarvamSTT:
    """Sarvam AI Speech-to-Text client."""

    def __init__(
        self,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
        model: str = "saaras:v4",
    ) -> None:
        self.api_key = api_key
        self.client = client
        self.model = model
        self._retry_delay: float = 1.0

    async def transcribe(
        self,
        audio: bytes,
        filename: str = "audio.wav",
        language_code: str = "unknown",
    ) -> Transcript:
        """Transcribe speech audio bytes to text using Sarvam STT.

        Args:
            audio: Audio bytes to transcribe.
            filename: Audio filename (used for content type detection).
            language_code: BCP-47 language code or "unknown" for auto-detection.

        Returns:
            Transcript containing transcribed text, language code, and raw response.

        Raises:
            SarvamError: On missing API key, network errors, or non-2xx responses.
        """
        api_key = self.api_key or get_settings().SARVAM_API_KEY
        if not api_key:
            raise SarvamError("SARVAM_API_KEY not set")

        if self.client is not None:
            return await self._transcribe_request(
                client=self.client,
                api_key=api_key,
                audio=audio,
                filename=filename,
                language_code=language_code,
            )

        async with httpx.AsyncClient(timeout=60.0) as client:
            return await self._transcribe_request(
                client=client,
                api_key=api_key,
                audio=audio,
                filename=filename,
                language_code=language_code,
            )

    async def _transcribe_request(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        audio: bytes,
        filename: str,
        language_code: str,
    ) -> Transcript:
        headers = {
            "api-subscription-key": api_key,
        }
        content_type = mimetypes.guess_type(filename)[0] or "audio/wav"
        files = {
            "file": (filename, audio, content_type),
        }
        data = {
            "model": self.model,
            "language_code": language_code,
        }

        try:
            response = await client.post(
                SARVAM_STT_URL,
                headers=headers,
                files=files,
                data=data,
            )
        except httpx.RequestError as exc:
            raise SarvamError(f"Sarvam STT connection error: {exc}") from exc

        # Retry once on 429 or 5xx after delay
        if response.status_code == 429 or response.status_code >= 500:
            await asyncio.sleep(self._retry_delay)
            files_retry = {
                "file": (filename, audio, content_type),
            }
            try:
                response = await client.post(
                    SARVAM_STT_URL,
                    headers=headers,
                    files=files_retry,
                    data=data,
                )
            except httpx.RequestError as exc:
                raise SarvamError(f"Sarvam STT connection error on retry: {exc}") from exc

        if not response.is_success:
            snippet = response.text[:300]
            raise SarvamError(
                f"Sarvam STT error ({response.status_code}): {snippet}",
                status_code=response.status_code,
            )

        try:
            payload = response.json()
        except Exception as exc:
            snippet = response.text[:300]
            raise SarvamError(
                f"Failed to parse Sarvam response JSON: {snippet}",
                status_code=response.status_code,
            ) from exc

        transcript_text = payload.get("transcript") or ""
        detected_lang = payload.get("language_code")
        final_lang = detected_lang if detected_lang else language_code

        return Transcript(
            text=transcript_text,
            language_code=final_lang,
            raw=payload,
        )
