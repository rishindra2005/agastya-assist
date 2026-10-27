"""Voice pipeline connecting audio STT/TTS to AgentSession."""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from typing import TYPE_CHECKING, Any

from agastya.voice.stt import SarvamSTT
from agastya.voice.tts import SarvamTTS
from agastya.voice.types import SarvamError, Transcript

if TYPE_CHECKING:
    from agastya.agent.events import AgentEvent
    from agastya.agent.session import AgentSession

logger = logging.getLogger(__name__)


def strip_markdown(text: str) -> str:
    """Strip markdown formatting from text for TTS synthesis.

    Removes bold/italic markers (**, __), inline code (`), header markers (#),
    leading bullet/numbered list markers (-, *, •, 1.), and replaces markdown
    links [label](url) with their label text. Collapses whitespace and leaves
    Devanagari and other non-Latin scripts intact.
    """
    if not text:
        return ""

    # Replace markdown links: [link text](url) -> link text
    text = re.sub(r"\[([^\]]+)\]\([^\s)]+\)", r"\1", text)

    # Remove leading list markers on each line:
    # matches line-leading -, *, •, or numbers with . or ) followed by whitespace or EOL
    text = re.sub(r"(?m)^\s*(?:[-*•]|\d+[.)])(?:\s+|$)", "", text)

    # Remove bold, inline code, and heading markers
    text = re.sub(r"\*{2,}", "", text)
    text = re.sub(r"_{2,}", "", text)
    text = text.replace("`", "")
    text = text.replace("#", "")

    # Collapse whitespace
    return " ".join(text.split())


class VoicePipeline:
    """Glue between audio and an AgentSession. Used by BOTH the web server and the CLI."""

    def __init__(
        self,
        session: AgentSession,
        stt: SarvamSTT | None = None,
        tts: SarvamTTS | None = None,
        auto_speak: bool = True,
    ) -> None:
        self.session = session
        self.stt = stt if stt is not None else SarvamSTT()
        self.tts = tts if tts is not None else SarvamTTS()
        self.auto_speak: bool = auto_speak
        self._task: asyncio.Task | None = None
        self._queue: asyncio.Queue[AgentEvent] | None = None

    async def start(self) -> None:
        """Subscribe to session.bus and launch background speaking task."""
        if self._task is not None and not self._task.done():
            return
        self._queue = self.session.bus.subscribe()
        self._task = asyncio.create_task(self._process_events())

    async def stop(self) -> None:
        """Cancel background speaking task and unsubscribe from bus."""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        if self._queue is not None:
            self.session.bus.unsubscribe(self._queue)
            self._queue = None

    async def __aenter__(self) -> VoicePipeline:
        await self.start()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.stop()

    async def handle_audio(
        self, audio: bytes, filename: str = "audio.wav"
    ) -> Transcript:
        """Transcribe speech audio and forward user message to session.

        Calls Sarvam STT with language_code='unknown' for auto-detection.
        Emits 'transcript' event on session.bus.
        If transcription is empty, emits status {'status': 'no_speech'}.
        Otherwise forwards transcribed text to session.send_user_message.
        On SarvamError, emits error event and returns an empty Transcript.
        """
        try:
            transcript = await self.stt.transcribe(
                audio, filename=filename, language_code="unknown"
            )
        except SarvamError as exc:
            self.session.bus.emit("error", error=str(exc), stage="stt")
            return Transcript(text="", language_code="unknown")

        self.session.bus.emit(
            "transcript",
            text=transcript.text,
            language_code=transcript.language_code,
        )

        if not transcript.text.strip():
            self.session.bus.emit("status", status="no_speech")
        else:
            await self.session.send_user_message(
                transcript.text, transcript.language_code
            )

        return transcript

    async def handle_text(
        self, text: str, language_code: str | None = None
    ) -> None:
        """Forward user text input to the agent session."""
        if not text or not text.strip():
            return
        await self.session.send_user_message(text, language_code=language_code)

    async def speak(self, text: str, language_code: str) -> bytes:
        """Synthesize text to speech WAV bytes, stripping markdown first."""
        cleaned = strip_markdown(text)
        if not cleaned.strip():
            return b""
        return await self.tts.synthesize(cleaned, language_code)

    async def _process_events(self) -> None:
        """Sequentially process bus events and synthesize speech."""
        if self._queue is None:
            return

        while True:
            try:
                event = await self._queue.get()
            except asyncio.CancelledError:
                break

            try:
                if not self.auto_speak:
                    continue

                if event.type not in ("assistant_text", "ask_user"):
                    continue

                raw_text = ""
                if event.type == "assistant_text":
                    raw_text = event.data.get("text", "")
                elif event.type == "ask_user":
                    raw_text = event.data.get("question", "")

                if not isinstance(raw_text, str):
                    continue

                cleaned = strip_markdown(raw_text)
                if not cleaned.strip():
                    continue

                lang = (
                    event.data.get("language_code")
                    or getattr(self.session, "language_code", None)
                    or "hi-IN"
                )

                try:
                    wav_bytes = await self.speak(cleaned, lang)
                except SarvamError as exc:
                    self.session.bus.emit("error", error=str(exc), stage="tts")
                    continue
                except Exception as exc:
                    self.session.bus.emit("error", error=str(exc), stage="tts")
                    continue

                if not wav_bytes:
                    continue

                audio_b64 = base64.b64encode(wav_bytes).decode("ascii")
                self.session.bus.emit(
                    "tts",
                    audio_b64=audio_b64,
                    mime="audio/wav",
                    text=cleaned,
                    language_code=lang,
                )
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.session.bus.emit("error", error=str(exc), stage="tts")
            finally:
                self._queue.task_done()
