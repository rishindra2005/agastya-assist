"""Voice pipeline connecting audio STT/TTS to AgentSession."""

from __future__ import annotations

import asyncio
import base64
import inspect
import logging
import re
import time
from typing import TYPE_CHECKING, Any, Callable

from agastya.voice.stt import SarvamSTT
from agastya.voice.tts import SarvamTTS
from agastya.voice.types import SarvamError, Transcript

if TYPE_CHECKING:
    from agastya.agent.events import AgentEvent
    from agastya.agent.session import AgentSession

logger = logging.getLogger(__name__)

NUM_DOT = "__NUMDOT__"
ABBR_DOT = "__ABBRDOT__"


def strip_markdown(text: str, keep_newlines: bool = False) -> str:
    """Strip markdown formatting from text for TTS synthesis.

    Removes bold/italic markers (**, __), inline code (`), header markers (#),
    leading bullet/numbered list markers (-, *, •, 1.), and replaces markdown
    links [label](url) with their label text. Collapses whitespace and leaves
    Devanagari and other non-Latin scripts intact. If keep_newlines is True,
    newlines between lines are preserved.
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

    if keep_newlines:
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
        return "\n".join(line for line in lines if line)

    # Collapse whitespace
    return " ".join(text.split())


def split_first_clause(sentences: list[str], max_clause_len: int = 60) -> list[str]:
    """Split the first sentence on its first comma if the leading clause is up to max_clause_len chars.

    This ensures the first audio chunk starts synthesizing and playing sooner,
    reducing time-to-first-audio (TTFA).
    """
    if not sentences:
        return []
    first = sentences[0]
    idx = first.find(",")
    if idx == -1:
        return sentences

    clause = first[: idx + 1].strip()
    remainder = first[idx + 1 :].strip()

    if not remainder or len(clause) > max_clause_len or not clause.replace(",", "").strip():
        return sentences

    if len(remainder) < 15 and len(sentences) > 1:
        merged_next = f"{remainder} {sentences[1]}"
        return [clause, merged_next] + sentences[2:]

    return [clause, remainder] + sentences[1:]


def split_sentences(text: str) -> list[str]:
    """Split text into spoken sentences for streaming TTS synthesis.

    Strips markdown first (preserving newlines). Splits on ।, ॥, ., ?, ! and newlines,
    keeping punctuation with its sentence. Never splits inside numbers (e.g. '1.5') or
    abbreviations (e.g. 'Mr.', 'Dr.', 'e.g.', 'i.e.'). Merges fragments shorter than
    25 characters into the next fragment (or previous if at the end).
    The first sentence is split at the first comma if <= 60 characters to reduce TTFA.
    """
    if not text:
        return []

    cleaned = strip_markdown(text, keep_newlines=True)
    if not cleaned.strip():
        return []

    token = cleaned

    # Protect periods inside numbers (e.g. 1.5, 3.14)
    token = re.sub(r"(?<=\d)\.(?=\d)", NUM_DOT, token)

    # Protect abbreviations with dots
    token = re.sub(r"(?i)\b(e)\s*\.\s*(g)\s*\.", rf"\1{ABBR_DOT}\2{ABBR_DOT}", token)
    token = re.sub(r"(?i)\b(i)\s*\.\s*(e)\s*\.", rf"\1{ABBR_DOT}\2{ABBR_DOT}", token)
    token = re.sub(
        r"(?i)\b(mr|mrs|ms|dr|prof|sr|jr|govt|no|rs|vs|etc|approx|dept|shri|smt)\.",
        rf"\1{ABBR_DOT}",
        token,
    )
    # Hindi honorific / common abbreviations with period
    token = re.sub(r"(डॉ|प्रो|पं|क्र|रु|श्री|श्रीमती)\.", rf"\1{ABBR_DOT}", token)

    # Delimiters:
    # 1. Devanagari danda / double danda
    # 2. Latin sentence punctuation (.?!), optionally followed by quotes/brackets
    # 3. Newlines
    delim_pattern = re.compile(r"([।॥]+|[.?!]+[\"'\)\]\}]*(?:\s+|\n+|$)|(?:\n+))")
    parts = delim_pattern.split(token)

    raw_sentences: list[str] = []
    current = ""
    for tok in parts:
        if not tok:
            continue
        if delim_pattern.fullmatch(tok):
            # Delimiter matched: keep punctuation chars, strip whitespace
            punct = re.sub(r"\s+", "", tok)
            current += punct
            c_clean = current.strip()
            if c_clean:
                raw_sentences.append(c_clean)
            current = ""
        else:
            current += tok

    if current.strip():
        raw_sentences.append(current.strip())

    # Restore protected dots
    restored = [
        s.replace(NUM_DOT, ".").replace(ABBR_DOT, ".").strip()
        for s in raw_sentences
    ]
    restored = [s for s in restored if s]

    # Merge fragments shorter than 25 chars into the next
    merged: list[str] = []
    carry = ""
    for s in restored:
        if carry:
            s = f"{carry} {s}"
            carry = ""
        if len(s) < 25:
            carry = s
        else:
            merged.append(s)
    if carry:
        if merged:
            merged[-1] = f"{merged[-1]} {carry}"
        else:
            merged.append(carry)

    merged = split_first_clause(merged, max_clause_len=60)
    return merged


class VoicePipeline:
    """Glue between audio and an AgentSession. Used by BOTH the web server and the CLI."""

    def __init__(
        self,
        session: AgentSession,
        stt: SarvamSTT | None = None,
        tts: SarvamTTS | None = None,
        auto_speak: bool = True,
        on_timing: Callable[..., Any] | None = None,
        use_streaming: bool = False,
    ) -> None:
        self.session = session
        self.stt = stt if stt is not None else SarvamSTT()
        self.tts = tts if tts is not None else SarvamTTS()
        self.auto_speak: bool = auto_speak
        self.on_timing: Callable[..., Any] | None = on_timing
        self.use_streaming: bool = use_streaming
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

    async def synthesize_stream(self, text: str, language_code: str) -> bytes:
        """Streaming synthesis via Sarvam WebSocket API, with fallback to HTTP TTS."""
        if not self.use_streaming:
            return await self.tts.synthesize(text, language_code)

        from agastya.config import get_settings

        api_key = getattr(self.tts, "api_key", None) or get_settings().SARVAM_API_KEY
        if not api_key:
            return await self.tts.synthesize(text, language_code)

        try:
            import json
            import websockets

            ws_url = "wss://api.sarvam.ai/text-to-speech/ws"
            headers = {"api-subscription-key": api_key}
            model = getattr(self.tts, "model", "bulbul:v3")
            speaker = getattr(self.tts, "speaker", "shubh")

            async with websockets.connect(ws_url, additional_headers=headers, timeout=10.0) as ws:
                config_msg = {
                    "type": "config",
                    "model": model,
                    "speaker": speaker,
                    "target_language_code": language_code,
                    "pace": 1.0,
                    "speech_sample_rate": 22050,
                }
                await ws.send(json.dumps(config_msg))
                text_msg = {"type": "text", "text": text}
                await ws.send(json.dumps(text_msg))
                flush_msg = {"type": "flush"}
                await ws.send(json.dumps(flush_msg))

                audio_chunks: list[bytes] = []
                while True:
                    msg = await asyncio.wait_for(ws.recv(), timeout=10.0)
                    data = (
                        json.loads(msg)
                        if isinstance(msg, str)
                        else json.loads(msg.decode("utf-8"))
                    )
                    if data.get("type") == "audio":
                        audio_b64 = data.get("audio", "")
                        if audio_b64:
                            audio_chunks.append(base64.b64decode(audio_b64))
                    if data.get("type") in ("completion", "done", "end"):
                        break

                if audio_chunks:
                    return b"".join(audio_chunks)
        except Exception as exc:
            logger.warning("Streaming TTS WS failed, falling back to HTTP synthesis: %s", exc)

        return await self.tts.synthesize(text, language_code)

    async def _synthesize_chunk(self, text: str, language_code: str) -> bytes:
        if self.use_streaming:
            return await self.synthesize_stream(text, language_code)
        return await self.tts.synthesize(text, language_code)

    async def speak(self, text: str, language_code: str) -> bytes:
        """Synthesize text to speech WAV bytes, stripping markdown first."""
        cleaned = strip_markdown(text)
        if not cleaned.strip():
            return b""
        return await self._synthesize_chunk(cleaned, language_code)

    async def _process_events(self) -> None:
        """Sequentially process bus events and synthesize sentence-streamed speech."""
        if self._queue is None:
            return

        while True:
            try:
                event = await self._queue.get()
            except asyncio.CancelledError:
                break

            current_task: asyncio.Task[bytes] | None = None
            next_task: asyncio.Task[bytes] | None = None

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

                if not isinstance(raw_text, str) or not raw_text.strip():
                    continue

                sentences = split_sentences(raw_text)
                if not sentences:
                    continue

                lang = (
                    event.data.get("language_code")
                    or getattr(self.session, "language_code", None)
                    or "hi-IN"
                )

                t0 = time.perf_counter()
                first_audio_recorded = False
                total = len(sentences)

                # Start synthesizing sentence 0; prefetch sentence 1 if exists
                current_task = asyncio.create_task(
                    self._synthesize_chunk(sentences[0], lang)
                )
                if total > 1:
                    next_task = asyncio.create_task(
                        self._synthesize_chunk(sentences[1], lang)
                    )

                for i in range(total):
                    sentence_text = sentences[i]
                    wav_bytes: bytes | None = None
                    try:
                        if current_task is not None:
                            wav_bytes = await current_task
                    except asyncio.CancelledError:
                        if next_task is not None and not next_task.done():
                            next_task.cancel()
                        raise
                    except SarvamError as exc:
                        self.session.bus.emit("error", error=str(exc), stage="tts")
                    except Exception as exc:
                        self.session.bus.emit("error", error=str(exc), stage="tts")

                    # Advance prefetch for next iteration
                    current_task = next_task
                    if i + 2 < total:
                        next_task = asyncio.create_task(
                            self._synthesize_chunk(sentences[i + 2], lang)
                        )
                    else:
                        next_task = None

                    if not wav_bytes:
                        continue

                    # Record TTFA for first successful audio emission
                    if not first_audio_recorded:
                        ttfa = time.perf_counter() - t0
                        first_audio_recorded = True
                        logger.debug(
                            "TTS time-to-first-audio: %.4fs for event %s (sentence 0: %r)",
                            ttfa,
                            event.type,
                            sentences[0],
                        )
                        if self.on_timing is not None:
                            try:
                                sig = inspect.signature(self.on_timing)
                                params = list(sig.parameters.values())
                                if len(params) == 1:
                                    p_name = params[0].name.lower()
                                    if any(k in p_name for k in ("dict", "data", "info", "timing")):
                                        self.on_timing({
                                            "event_type": event.type,
                                            "ttfa": ttfa,
                                            "seq": i,
                                            "sentences": total,
                                        })
                                    else:
                                        self.on_timing(ttfa)
                                elif len(params) == 2:
                                    self.on_timing(event.type, ttfa)
                                else:
                                    self.on_timing(ttfa)
                            except Exception as exc:
                                logger.debug("on_timing callback error: %s", exc)

                    audio_b64 = base64.b64encode(wav_bytes).decode("ascii")
                    self.session.bus.emit(
                        "tts",
                        audio_b64=audio_b64,
                        mime="audio/wav",
                        text=sentence_text,
                        language_code=lang,
                        seq=i,
                        final=(i == total - 1),
                    )

            except asyncio.CancelledError:
                if current_task is not None and not current_task.done():
                    current_task.cancel()
                if next_task is not None and not next_task.done():
                    next_task.cancel()
                break
            except Exception as exc:
                self.session.bus.emit("error", error=str(exc), stage="tts")
            finally:
                self._queue.task_done()
