"""Unit tests for VoicePipeline and strip_markdown."""

from __future__ import annotations

import asyncio
import base64
import pytest

from agastya.agent.events import AgentEvent, EventBus
from agastya.voice.pipeline import VoicePipeline, strip_markdown
from agastya.voice.stt import SarvamSTT
from agastya.voice.tts import SarvamTTS
from agastya.voice.types import SarvamError, Transcript


class FakeSession:
    """Mock AgentSession with an EventBus and user message recorder."""

    def __init__(self, language_code: str = "hi-IN") -> None:
        self.bus = EventBus()
        self.language_code = language_code
        self.user_messages: list[tuple[str, str | None]] = []

    async def send_user_message(
        self, text: str, language_code: str | None = None
    ) -> None:
        self.user_messages.append((text, language_code))


class FakeSTT:
    """Mock SarvamSTT client."""

    def __init__(
        self,
        transcript: Transcript | None = None,
        error: Exception | None = None,
    ) -> None:
        self.transcript = transcript or Transcript(
            text="नमस्ते दुनिया", language_code="hi-IN"
        )
        self.error = error
        self.calls: list[dict] = []

    async def transcribe(
        self,
        audio: bytes,
        filename: str = "audio.wav",
        language_code: str = "unknown",
    ) -> Transcript:
        self.calls.append(
            {"audio": audio, "filename": filename, "language_code": language_code}
        )
        if self.error is not None:
            raise self.error
        return self.transcript


class FakeTTS:
    """Mock SarvamTTS client."""

    def __init__(
        self,
        audio: bytes = b"RIFFfake_wav_audio_bytes",
        error: Exception | None = None,
    ) -> None:
        self.audio = audio
        self.error = error
        self.calls: list[dict] = []
        self.errors_to_raise: list[Exception] = []

    async def synthesize(self, text: str, language_code: str) -> bytes:
        self.calls.append({"text": text, "language_code": language_code})
        if self.errors_to_raise:
            raise self.errors_to_raise.pop(0)
        if self.error is not None:
            raise self.error
        return self.audio


# ============================================================================
# strip_markdown tests
# ============================================================================


def test_strip_markdown_english() -> None:
    text = (
        "# Portal Instructions\n"
        "Please visit [Income Tax Portal](https://incometax.gov.in) to file.\n"
        "Here are the **steps** to follow:\n"
        "- Step one: enter `PAN`\n"
        "- Step two: enter __password__\n"
        "• Bullet note\n"
        "1. First numbered item\n"
        "2. Second numbered item\n"
    )
    stripped = strip_markdown(text)
    assert "**" not in stripped
    assert "__" not in stripped
    assert "`" not in stripped
    assert "#" not in stripped
    assert "https://incometax.gov.in" not in stripped
    assert "Income Tax Portal" in stripped
    assert "- Step" not in stripped
    assert "Step one: enter PAN" in stripped
    assert "Bullet note" in stripped
    assert "First numbered item" in stripped
    assert "Second numbered item" in stripped
    assert "\n" not in stripped


def test_strip_markdown_hindi_and_devanagari() -> None:
    text = (
        "## महत्वपूर्ण सूचना\n"
        "कृपया [यहाँ क्लिक करें](https://uidai.gov.in) और **विवरण** जांचें।\n"
        "- पहला कदम: `आधार संख्या` दर्ज करें\n"
        "• दूसरा कदम: __ओटीपी__ साझा न करें।\n"
        "1. अंतिम चरण पूरा करें।"
    )
    stripped = strip_markdown(text)
    assert "महत्वपूर्ण सूचना" in stripped
    assert "यहाँ क्लिक करें" in stripped
    assert "विवरण जांचें।" in stripped
    assert "पहला कदम: आधार संख्या दर्ज करें" in stripped
    assert "दूसरा कदम: ओटीपी साझा न करें।" in stripped
    assert "अंतिम चरण पूरा करें।" in stripped
    assert "https://uidai.gov.in" not in stripped
    assert "**" not in stripped
    assert "__" not in stripped
    assert "`" not in stripped
    assert "#" not in stripped
    assert "\n" not in stripped


def test_strip_markdown_edge_cases() -> None:
    assert strip_markdown("") == ""
    assert strip_markdown("   \n   \t  ") == ""
    assert strip_markdown("** **") == ""
    assert strip_markdown("## ###") == ""
    assert strip_markdown("` `") == ""
    assert strip_markdown("- \n* \n• \n1. ") == ""

    # Math and snake_case should be preserved
    assert strip_markdown("Math: 5 * 5 = 25 and 10 - 2 = 8") == "Math: 5 * 5 = 25 and 10 - 2 = 8"
    assert strip_markdown("Field: user_id and pan_number") == "Field: user_id and pan_number"


# ============================================================================
# handle_audio tests
# ============================================================================


@pytest.mark.asyncio
async def test_handle_audio_forwarded_with_detected_language() -> None:
    session = FakeSession(language_code="en-IN")
    stt = FakeSTT(
        transcript=Transcript(
            text="मेरा नाम राज है",
            language_code="hi-IN",
            raw={"confidence": 0.95},
        )
    )
    tts = FakeTTS()
    pipeline = VoicePipeline(session=session, stt=stt, tts=tts)

    queue = session.bus.subscribe()

    transcript = await pipeline.handle_audio(b"audio_bytes", filename="user.wav")

    assert transcript.text == "मेरा नाम राज है"
    assert transcript.language_code == "hi-IN"
    assert len(stt.calls) == 1
    assert stt.calls[0]["filename"] == "user.wav"
    assert stt.calls[0]["language_code"] == "unknown"

    # Verify session received message
    assert session.user_messages == [("मेरा नाम राज है", "hi-IN")]

    # Verify transcript event emitted on bus
    event = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert event.type == "transcript"
    assert event.data == {"text": "मेरा नाम राज है", "language_code": "hi-IN"}


@pytest.mark.asyncio
async def test_handle_audio_no_speech() -> None:
    session = FakeSession()
    stt = FakeSTT(transcript=Transcript(text="   ", language_code="hi-IN"))
    tts = FakeTTS()
    pipeline = VoicePipeline(session=session, stt=stt, tts=tts)

    queue = session.bus.subscribe()

    transcript = await pipeline.handle_audio(b"silent_bytes")

    assert transcript.text == "   "
    # User message must NOT be sent to session
    assert session.user_messages == []

    # Should have emitted 'transcript' event followed by 'status' no_speech
    ev1 = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert ev1.type == "transcript"

    ev2 = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert ev2.type == "status"
    assert ev2.data == {"status": "no_speech"}


@pytest.mark.asyncio
async def test_handle_audio_stt_error() -> None:
    session = FakeSession()
    stt = FakeSTT(error=SarvamError("STT API failed", status_code=500))
    tts = FakeTTS()
    pipeline = VoicePipeline(session=session, stt=stt, tts=tts)

    queue = session.bus.subscribe()

    transcript = await pipeline.handle_audio(b"bad_audio")

    # Should return empty transcript
    assert transcript.text == ""
    assert transcript.language_code == "unknown"
    assert session.user_messages == []

    # Should emit error event
    ev = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert ev.type == "error"
    assert "STT API failed" in ev.data["error"]
    assert ev.data["stage"] == "stt"


# ============================================================================
# handle_text and speak tests
# ============================================================================


@pytest.mark.asyncio
async def test_handle_text() -> None:
    session = FakeSession()
    pipeline = VoicePipeline(session=session, stt=FakeSTT(), tts=FakeTTS())

    await pipeline.handle_text("नमस्ते", language_code="hi-IN")
    assert session.user_messages == [("नमस्ते", "hi-IN")]

    # Empty text ignored
    await pipeline.handle_text("   ")
    assert len(session.user_messages) == 1


@pytest.mark.asyncio
async def test_speak_strips_markdown() -> None:
    session = FakeSession()
    tts = FakeTTS(audio=b"RIFFsynthesized_wav")
    pipeline = VoicePipeline(session=session, stt=FakeSTT(), tts=tts)

    wav = await pipeline.speak("**नमस्ते** [लिंक](http://test)", "hi-IN")
    assert wav == b"RIFFsynthesized_wav"
    assert len(tts.calls) == 1
    assert tts.calls[0]["text"] == "नमस्ते लिंक"
    assert tts.calls[0]["language_code"] == "hi-IN"

    # Blank text returns empty bytes and does not invoke TTS
    wav_empty = await pipeline.speak("** **", "hi-IN")
    assert wav_empty == b""
    assert len(tts.calls) == 1


# ============================================================================
# auto_speak and background loop tests
# ============================================================================


@pytest.mark.asyncio
async def test_auto_speak_produces_tts_events_in_order() -> None:
    session = FakeSession(language_code="hi-IN")
    wav1 = b"RIFFaudio_one"
    wav2 = b"RIFFaudio_two"

    tts = FakeTTS()
    # Return wav1 then wav2
    responses = [wav1, wav2]

    async def fake_synthesize(text: str, language_code: str) -> bytes:
        tts.calls.append({"text": text, "language_code": language_code})
        return responses.pop(0)

    tts.synthesize = fake_synthesize  # type: ignore

    pipeline = VoicePipeline(session=session, stt=FakeSTT(), tts=tts)
    queue = session.bus.subscribe()

    await pipeline.start()
    try:
        session.bus.emit("assistant_text", text="**पहला संदेश**", language_code="hi-IN")
        session.bus.emit("assistant_text", text="दूसरा संदेश", language_code="hi-IN")

        # Collect events until we get both tts events
        tts_events: list[AgentEvent] = []
        for _ in range(10):
            ev = await asyncio.wait_for(queue.get(), timeout=2.0)
            if ev.type == "tts":
                tts_events.append(ev)
                if len(tts_events) == 2:
                    break

        assert len(tts_events) == 2
        assert tts_events[0].data["text"] == "पहला संदेश"
        assert tts_events[0].data["language_code"] == "hi-IN"
        assert tts_events[0].data["mime"] == "audio/wav"
        assert base64.b64decode(tts_events[0].data["audio_b64"]) == wav1

        assert tts_events[1].data["text"] == "दूसरा संदेश"
        assert tts_events[1].data["language_code"] == "hi-IN"
        assert base64.b64decode(tts_events[1].data["audio_b64"]) == wav2

        assert [c["text"] for c in tts.calls] == ["पहला संदेश", "दूसरा संदेश"]
    finally:
        await pipeline.stop()


@pytest.mark.asyncio
async def test_auto_speak_ask_user_event() -> None:
    session = FakeSession(language_code="hi-IN")
    tts = FakeTTS(audio=b"RIFFask_user_wav")
    pipeline = VoicePipeline(session=session, stt=FakeSTT(), tts=tts)
    queue = session.bus.subscribe()

    await pipeline.start()
    try:
        session.bus.emit(
            "ask_user",
            question="आपका पैन नंबर क्या है?",
            question_id="q1",
            language_code="hi-IN",
        )

        tts_event: AgentEvent | None = None
        for _ in range(5):
            ev = await asyncio.wait_for(queue.get(), timeout=2.0)
            if ev.type == "tts":
                tts_event = ev
                break

        assert tts_event is not None
        assert tts_event.data["text"] == "आपका पैन नंबर क्या है?"
        assert tts_event.data["language_code"] == "hi-IN"
        assert base64.b64decode(tts_event.data["audio_b64"]) == b"RIFFask_user_wav"
    finally:
        await pipeline.stop()


@pytest.mark.asyncio
async def test_auto_speak_false_produces_none() -> None:
    session = FakeSession(language_code="hi-IN")
    tts = FakeTTS()
    pipeline = VoicePipeline(session=session, stt=FakeSTT(), tts=tts, auto_speak=False)
    assert pipeline.auto_speak is False
    queue = session.bus.subscribe()

    await pipeline.start()
    try:
        session.bus.emit("assistant_text", text="नमस्ते", language_code="hi-IN")

        # Give loop a brief moment to process
        await asyncio.sleep(0.05)
        assert len(tts.calls) == 0

        # Verify only the assistant_text event is on queue, no tts event
        ev = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert ev.type == "assistant_text"
        assert queue.empty()
    finally:
        await pipeline.stop()


@pytest.mark.asyncio
async def test_tts_error_emits_error_and_loop_keeps_going() -> None:
    session = FakeSession(language_code="hi-IN")
    tts = FakeTTS(audio=b"RIFFsecond_wav")
    tts.errors_to_raise = [SarvamError("TTS voice failed", status_code=500)]

    pipeline = VoicePipeline(session=session, stt=FakeSTT(), tts=tts)
    queue = session.bus.subscribe()

    await pipeline.start()
    try:
        # First message will trigger TTS error
        session.bus.emit("assistant_text", text="विफल संदेश", language_code="hi-IN")
        # Second message will succeed
        session.bus.emit("assistant_text", text="सफल संदेश", language_code="hi-IN")

        error_event: AgentEvent | None = None
        tts_event: AgentEvent | None = None

        for _ in range(10):
            ev = await asyncio.wait_for(queue.get(), timeout=2.0)
            if ev.type == "error" and ev.data.get("stage") == "tts":
                error_event = ev
            elif ev.type == "tts":
                tts_event = ev
                break

        assert error_event is not None
        assert "TTS voice failed" in error_event.data["error"]
        assert error_event.data["stage"] == "tts"

        assert tts_event is not None
        assert tts_event.data["text"] == "सफल संदेश"
        assert base64.b64decode(tts_event.data["audio_b64"]) == b"RIFFsecond_wav"
    finally:
        await pipeline.stop()


@pytest.mark.asyncio
async def test_auto_speak_skips_blank_text() -> None:
    session = FakeSession(language_code="hi-IN")
    tts = FakeTTS(audio=b"RIFFvalid")
    pipeline = VoicePipeline(session=session, stt=FakeSTT(), tts=tts)
    queue = session.bus.subscribe()

    await pipeline.start()
    try:
        # Assistant sends markdown that strips to empty string
        session.bus.emit("assistant_text", text="**   **", language_code="hi-IN")
        session.bus.emit("assistant_text", text="वास्तविक संदेश", language_code="hi-IN")

        tts_event: AgentEvent | None = None
        for _ in range(10):
            ev = await asyncio.wait_for(queue.get(), timeout=2.0)
            if ev.type == "tts":
                tts_event = ev
                break

        assert tts_event is not None
        assert tts_event.data["text"] == "वास्तविक संदेश"
        # TTS synthesize should have only been called once (for the non-blank message)
        assert len(tts.calls) == 1
        assert tts.calls[0]["text"] == "वास्तविक संदेश"
    finally:
        await pipeline.stop()


@pytest.mark.asyncio
async def test_pipeline_default_init() -> None:
    session = FakeSession()
    pipeline = VoicePipeline(session=session)
    assert pipeline.session is session
    assert isinstance(pipeline.stt, SarvamSTT)
    assert isinstance(pipeline.tts, SarvamTTS)
    assert pipeline.auto_speak is True


@pytest.mark.asyncio
async def test_auto_speak_language_code_fallback() -> None:
    session = FakeSession(language_code="ta-IN")
    tts = FakeTTS(audio=b"RIFFtamil_wav")
    pipeline = VoicePipeline(session=session, stt=FakeSTT(), tts=tts)
    queue = session.bus.subscribe()

    await pipeline.start()
    try:
        # Emit without language_code so it falls back to session.language_code
        session.bus.emit("assistant_text", text="வணக்கம்")

        tts_event: AgentEvent | None = None
        for _ in range(5):
            ev = await asyncio.wait_for(queue.get(), timeout=2.0)
            if ev.type == "tts":
                tts_event = ev
                break

        assert tts_event is not None
        assert tts_event.data["text"] == "வணக்கம்"
        assert tts_event.data["language_code"] == "ta-IN"
        assert len(tts.calls) == 1
        assert tts.calls[0]["language_code"] == "ta-IN"
    finally:
        await pipeline.stop()


@pytest.mark.asyncio
async def test_pipeline_start_idempotent() -> None:
    session = FakeSession()
    pipeline = VoicePipeline(session=session, stt=FakeSTT(), tts=FakeTTS())
    await pipeline.start()
    task1 = pipeline._task
    # Second start call should not recreate task or queue
    await pipeline.start()
    assert pipeline._task is task1
    await pipeline.stop()
    assert pipeline._task is None
    # Stopping again is also safe
    await pipeline.stop()


@pytest.mark.asyncio
async def test_pipeline_context_manager() -> None:
    session = FakeSession()
    pipeline = VoicePipeline(session=session, stt=FakeSTT(), tts=FakeTTS())
    async with pipeline:
        assert pipeline._task is not None
        assert not pipeline._task.done()

    assert pipeline._task is None
    assert pipeline._queue is None
