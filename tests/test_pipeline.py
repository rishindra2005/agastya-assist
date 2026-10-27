"""Unit tests for VoicePipeline, split_sentences, and strip_markdown."""

from __future__ import annotations

import asyncio
import base64
import time
from typing import Any, Callable
import pytest

from agastya.agent.events import AgentEvent, EventBus
from agastya.voice.pipeline import VoicePipeline, split_sentences, strip_markdown
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


class SleepingFakeTTS:
    """Mock SarvamTTS client that records calls, timestamps, and simulates latency."""

    def __init__(
        self,
        delay: float = 0.05,
        audio: bytes = b"RIFFfake_wav_audio_bytes",
        errors_by_text: dict[str, Exception] | None = None,
        delay_fn: Callable[[str], float] | None = None,
    ) -> None:
        self.delay = delay
        self.delay_fn = delay_fn
        self.audio = audio
        self.errors_by_text = errors_by_text or {}
        self.calls: list[dict[str, Any]] = []

    async def synthesize(self, text: str, language_code: str) -> bytes:
        t_start = time.perf_counter()
        call_info: dict[str, Any] = {
            "text": text,
            "language_code": language_code,
            "start": t_start,
            "finish": None,
        }
        self.calls.append(call_info)

        sleep_time = self.delay_fn(text) if self.delay_fn is not None else self.delay
        if sleep_time > 0:
            await asyncio.sleep(sleep_time)

        call_info["finish"] = time.perf_counter()

        if text in self.errors_by_text:
            raise self.errors_by_text[text]

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
# split_sentences tests
# ============================================================================


def test_split_sentences_basic_punctuation() -> None:
    text = (
        "This is the first sentence that is long enough. "
        "Is this the second sentence that is also long enough? "
        "Yes, this is the third sentence and it is long enough!"
    )
    result = split_sentences(text)
    assert len(result) == 3
    assert result[0] == "This is the first sentence that is long enough."
    assert result[1] == "Is this the second sentence that is also long enough?"
    assert result[2] == "Yes, this is the third sentence and it is long enough!"


def test_split_sentences_hindi_danda() -> None:
    text = (
        "यह पहला वाक्य है जो पच्चीस अक्षरों से लंबा है। "
        "यह दूसरा वाक्य भी पच्चीस अक्षरों से काफी लंबा है। "
        "यह अंतिम वाक्य समाप्त हुआ॥"
    )
    result = split_sentences(text)
    assert len(result) == 3
    assert result[0] == "यह पहला वाक्य है जो पच्चीस अक्षरों से लंबा है।"
    assert result[1] == "यह दूसरा वाक्य भी पच्चीस अक्षरों से काफी लंबा है।"
    assert result[2] == "यह अंतिम वाक्य समाप्त हुआ॥"


def test_split_sentences_preserves_numbers() -> None:
    text = (
        "The current interest rate is 7.5 percent per annum. "
        "Your total balance is 1.5 lakh rupees in your account."
    )
    result = split_sentences(text)
    assert len(result) == 2
    assert "7.5" in result[0]
    assert "1.5" in result[1]
    assert result[0] == "The current interest rate is 7.5 percent per annum."
    assert result[1] == "Your total balance is 1.5 lakh rupees in your account."


def test_split_sentences_preserves_abbreviations() -> None:
    text = (
        "Please consult Dr. Sharma or Mr. Patel before filing. "
        "Please provide identification, e.g. your PAN card or Aadhaar card."
    )
    result = split_sentences(text)
    assert len(result) == 2
    assert result[0] == "Please consult Dr. Sharma or Mr. Patel before filing."
    assert result[1] == "Please provide identification, e.g. your PAN card or Aadhaar card."


def test_split_sentences_newlines() -> None:
    text = (
        "First line contains plenty of characters to exceed limit\n"
        "Second line also contains plenty of characters to exceed limit."
    )
    result = split_sentences(text)
    assert len(result) == 2
    assert result[0] == "First line contains plenty of characters to exceed limit"
    assert result[1] == "Second line also contains plenty of characters to exceed limit."


def test_split_sentences_merges_short_fragments_into_next() -> None:
    # "Yes!" is 4 chars (< 25) -> merges into the next sentence
    text = "Yes! This is a longer sentence that definitely has more than 25 characters."
    result = split_sentences(text)
    assert len(result) == 1
    assert result[0] == "Yes! This is a longer sentence that definitely has more than 25 characters."

    # Multiple short fragments: "OK." (3) and "Wait." (5) merge into the next
    text2 = "OK. Wait. Here is a sufficiently long sentence that has enough characters."
    result2 = split_sentences(text2)
    assert len(result2) == 1
    assert result2[0] == "OK. Wait. Here is a sufficiently long sentence that has enough characters."

    # Middle short fragment merges into next:
    text3 = (
        "Here is the first long sentence with plenty of characters. "
        "Sure. "
        "Here is the third long sentence with plenty of characters."
    )
    result3 = split_sentences(text3)
    assert len(result3) == 2
    assert result3[0] == "Here is the first long sentence with plenty of characters."
    assert result3[1] == "Sure. Here is the third long sentence with plenty of characters."


def test_split_sentences_trailing_short_fragment() -> None:
    text = "Here is a long sentence that definitely has more than 25 characters. Thanks."
    result = split_sentences(text)
    assert len(result) == 1
    assert result[0] == "Here is a long sentence that definitely has more than 25 characters. Thanks."


def test_split_sentences_strips_markdown() -> None:
    text = (
        "**पहला निर्देश:** कृपया अपना [पैन कार्ड](https://example.com) नंबर दर्ज करें। "
        "इसके बाद `OTP` सत्यापन पूरा करना आवश्यक होगा।"
    )
    result = split_sentences(text)
    assert len(result) == 2
    assert "**" not in result[0]
    assert "https://example.com" not in result[0]
    assert "पहला निर्देश: कृपया अपना पैन कार्ड नंबर दर्ज करें।" == result[0]
    assert "`" not in result[1]
    assert "इसके बाद OTP सत्यापन पूरा करना आवश्यक होगा।" == result[1]


def test_split_sentences_empty_and_blank() -> None:
    assert split_sentences("") == []
    assert split_sentences("   \n   \t  ") == []
    assert split_sentences("**   **") == []


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
        assert tts_events[0].data["seq"] == 0
        assert tts_events[0].data["final"] is True
        assert base64.b64decode(tts_events[0].data["audio_b64"]) == wav1

        assert tts_events[1].data["text"] == "दूसरा संदेश"
        assert tts_events[1].data["language_code"] == "hi-IN"
        assert tts_events[1].data["seq"] == 0
        assert tts_events[1].data["final"] is True
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
        assert tts_event.data["seq"] == 0
        assert tts_event.data["final"] is True
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


# ============================================================================
# Sentence-streaming speak loop tests (Batch D)
# ============================================================================


@pytest.mark.asyncio
async def test_sentence_streaming_seq_and_final() -> None:
    session = FakeSession(language_code="hi-IN")
    tts = SleepingFakeTTS(delay=0.01)
    pipeline = VoicePipeline(session=session, stt=FakeSTT(), tts=tts)
    queue = session.bus.subscribe()

    text = (
        "पहला वाक्य पच्चीस अक्षरों से अधिक लंबा है। "
        "दूसरा वाक्य भी पच्चीस अक्षरों से अधिक लंबा है। "
        "तीसरा वाक्य भी पच्चीस अक्षरों से अधिक लंबा है।"
    )

    await pipeline.start()
    try:
        session.bus.emit("assistant_text", text=text, language_code="hi-IN")

        tts_events: list[AgentEvent] = []
        for _ in range(10):
            ev = await asyncio.wait_for(queue.get(), timeout=2.0)
            if ev.type == "tts":
                tts_events.append(ev)
                if len(tts_events) == 3:
                    break

        assert len(tts_events) == 3

        # seq must increment from 0 to 2
        assert [ev.data["seq"] for ev in tts_events] == [0, 1, 2]

        # final must be False, False, True
        assert [ev.data["final"] for ev in tts_events] == [False, False, True]

        # order of text emitted must match split sentences
        sentences = split_sentences(text)
        assert [ev.data["text"] for ev in tts_events] == sentences
    finally:
        await pipeline.stop()


@pytest.mark.asyncio
async def test_single_sentence_seq_zero_final_true() -> None:
    session = FakeSession(language_code="hi-IN")
    tts = SleepingFakeTTS(delay=0.01)
    pipeline = VoicePipeline(session=session, stt=FakeSTT(), tts=tts)
    queue = session.bus.subscribe()

    await pipeline.start()
    try:
        session.bus.emit(
            "assistant_text",
            text="यह केवल एक ही वाक्य है जो पच्चीस अक्षरों से लंबा है।",
            language_code="hi-IN",
        )

        tts_event: AgentEvent | None = None
        for _ in range(5):
            ev = await asyncio.wait_for(queue.get(), timeout=2.0)
            if ev.type == "tts":
                tts_event = ev
                break

        assert tts_event is not None
        assert tts_event.data["seq"] == 0
        assert tts_event.data["final"] is True
    finally:
        await pipeline.stop()


@pytest.mark.asyncio
async def test_sentence_streaming_prefetch_overlaps() -> None:
    session = FakeSession(language_code="hi-IN")
    # Each call sleeps 0.05 seconds (proportional sleep per call)
    tts = SleepingFakeTTS(delay=0.05)
    pipeline = VoicePipeline(session=session, stt=FakeSTT(), tts=tts)
    queue = session.bus.subscribe()

    text = (
        "पहला वाक्य पच्चीस अक्षरों से अधिक लंबा है। "
        "दूसरा वाक्य भी पच्चीस अक्षरों से अधिक लंबा है।"
    )

    await pipeline.start()
    try:
        session.bus.emit("assistant_text", text=text, language_code="hi-IN")

        tts_events: list[AgentEvent] = []
        for _ in range(10):
            ev = await asyncio.wait_for(queue.get(), timeout=2.0)
            if ev.type == "tts":
                tts_events.append(ev)
                if len(tts_events) == 2:
                    break

        assert len(tts_events) == 2
        assert len(tts.calls) == 2

        # Verify prefetch overlap:
        # Call 1 must have started before Call 0 finished!
        call0_finish = tts.calls[0]["finish"]
        call1_start = tts.calls[1]["start"]
        assert call1_start < call0_finish, (
            f"Expected prefetch overlap: call 1 started at {call1_start}, "
            f"but call 0 finished at {call0_finish}"
        )

        # Total time for 2 calls of 0.05s sequential would be >= 0.10s.
        # With prefetch overlap, synthesis of call 1 runs concurrently with call 0,
        # so total synth duration < sequential sum.
        sequential_sum = (tts.calls[0]["finish"] - tts.calls[0]["start"]) + (
            tts.calls[1]["finish"] - tts.calls[1]["start"]
        )
        total_synth_duration = max(tts.calls[0]["finish"], tts.calls[1]["finish"]) - min(
            tts.calls[0]["start"], tts.calls[1]["start"]
        )
        assert total_synth_duration < sequential_sum, (
            f"Expected total synth time ({total_synth_duration:.3f}s) "
            f"to be less than sequential sum ({sequential_sum:.3f}s)"
        )
    finally:
        await pipeline.stop()


@pytest.mark.asyncio
async def test_sentence_streaming_error_in_middle_sentence() -> None:
    session = FakeSession(language_code="hi-IN")
    error_sentence = "दूसरा वाक्य भी पच्चीस अक्षरों से अधिक लंबा है।"
    tts = SleepingFakeTTS(
        delay=0.01,
        errors_by_text={error_sentence: SarvamError("Middle sentence failed", status_code=500)},
    )
    pipeline = VoicePipeline(session=session, stt=FakeSTT(), tts=tts)
    queue = session.bus.subscribe()

    text = (
        "पहला वाक्य पच्चीस अक्षरों से अधिक लंबा है। "
        "दूसरा वाक्य भी पच्चीस अक्षरों से अधिक लंबा है। "
        "तीसरा वाक्य भी पच्चीस अक्षरों से अधिक लंबा है।"
    )

    await pipeline.start()
    try:
        session.bus.emit("assistant_text", text=text, language_code="hi-IN")

        events: list[AgentEvent] = []
        for _ in range(15):
            ev = await asyncio.wait_for(queue.get(), timeout=2.0)
            if ev.type in ("tts", "error"):
                events.append(ev)
                if len(events) == 3:
                    break

        assert len(events) == 3

        # Event 0: TTS for sentence 0 (seq=0, final=False)
        assert events[0].type == "tts"
        assert events[0].data["seq"] == 0
        assert events[0].data["final"] is False
        assert "पहला वाक्य" in events[0].data["text"]

        # Event 1: Error event for sentence 1
        assert events[1].type == "error"
        assert events[1].data["stage"] == "tts"
        assert "Middle sentence failed" in events[1].data["error"]

        # Event 2: TTS for sentence 2 (seq=2, final=True)
        assert events[2].type == "tts"
        assert events[2].data["seq"] == 2
        assert events[2].data["final"] is True
        assert "तीसरा वाक्य" in events[2].data["text"]
    finally:
        await pipeline.stop()


@pytest.mark.asyncio
async def test_strict_ordering_across_source_events() -> None:
    session = FakeSession(language_code="hi-IN")
    tts = SleepingFakeTTS(delay=0.02)
    pipeline = VoicePipeline(session=session, stt=FakeSTT(), tts=tts)
    queue = session.bus.subscribe()

    msg1 = "संदेश एक का पहला लंबा वाक्य यहाँ है। संदेश एक का दूसरा लंबा वाक्य यहाँ है।"
    msg2 = "संदेश दो का पहला लंबा वाक्य यहाँ है। संदेश दो का दूसरा लंबा वाक्य यहाँ है।"

    await pipeline.start()
    try:
        session.bus.emit("assistant_text", text=msg1, language_code="hi-IN")
        session.bus.emit("assistant_text", text=msg2, language_code="hi-IN")

        tts_events: list[AgentEvent] = []
        for _ in range(20):
            ev = await asyncio.wait_for(queue.get(), timeout=3.0)
            if ev.type == "tts":
                tts_events.append(ev)
                if len(tts_events) == 4:
                    break

        assert len(tts_events) == 4

        # Message 1 sentences
        assert tts_events[0].data["seq"] == 0
        assert tts_events[0].data["final"] is False
        assert "संदेश एक का पहला" in tts_events[0].data["text"]

        assert tts_events[1].data["seq"] == 1
        assert tts_events[1].data["final"] is True
        assert "संदेश एक का दूसरा" in tts_events[1].data["text"]

        # Message 2 sentences (seq resets to 0)
        assert tts_events[2].data["seq"] == 0
        assert tts_events[2].data["final"] is False
        assert "संदेश दो का पहला" in tts_events[2].data["text"]

        assert tts_events[3].data["seq"] == 1
        assert tts_events[3].data["final"] is True
        assert "संदेश दो का दूसरा" in tts_events[3].data["text"]
    finally:
        await pipeline.stop()


@pytest.mark.asyncio
async def test_on_timing_callback_invoked() -> None:
    session = FakeSession(language_code="hi-IN")
    timing_records: list[dict[str, Any]] = []

    def timing_cb(data: dict[str, Any]) -> None:
        timing_records.append(data)

    tts = SleepingFakeTTS(delay=0.02)
    pipeline = VoicePipeline(
        session=session, stt=FakeSTT(), tts=tts, on_timing=timing_cb
    )
    queue = session.bus.subscribe()

    text = "पहला वाक्य पच्चीस अक्षरों से अधिक लंबा है। दूसरा वाक्य भी पच्चीस अक्षरों से अधिक लंबा है।"

    await pipeline.start()
    try:
        session.bus.emit("assistant_text", text=text, language_code="hi-IN")

        for _ in range(10):
            ev = await asyncio.wait_for(queue.get(), timeout=2.0)
            if ev.type == "tts" and ev.data.get("final"):
                break

        assert len(timing_records) == 1
        assert timing_records[0]["event_type"] == "assistant_text"
        assert timing_records[0]["ttfa"] > 0
        assert timing_records[0]["sentences"] == 2
    finally:
        await pipeline.stop()


@pytest.mark.asyncio
async def test_sentence_streaming_prefetch_proportional_length_sleep() -> None:
    session = FakeSession(language_code="hi-IN")
    # Proportional sleep: 0.001s per character
    tts = SleepingFakeTTS(delay_fn=lambda text: 0.001 * len(text))
    pipeline = VoicePipeline(session=session, stt=FakeSTT(), tts=tts)
    queue = session.bus.subscribe()

    text = (
        "पहला वाक्य पच्चीस अक्षरों से अधिक लंबा होना चाहिए। "
        "दूसरा वाक्य भी पच्चीस अक्षरों से अधिक लंबा होना चाहिए।"
    )

    await pipeline.start()
    try:
        session.bus.emit("assistant_text", text=text, language_code="hi-IN")

        tts_events: list[AgentEvent] = []
        for _ in range(10):
            ev = await asyncio.wait_for(queue.get(), timeout=2.0)
            if ev.type == "tts":
                tts_events.append(ev)
                if len(tts_events) == 2:
                    break

        assert len(tts_events) == 2
        assert len(tts.calls) == 2

        # Verify prefetch overlap:
        # Call 1 must have started before Call 0 finished!
        assert tts.calls[1]["start"] < tts.calls[0]["finish"]

        sequential_sum = sum(c["finish"] - c["start"] for c in tts.calls)
        total_time = max(c["finish"] for c in tts.calls) - min(c["start"] for c in tts.calls)
        assert total_time < sequential_sum
    finally:
        await pipeline.stop()


@pytest.mark.asyncio
async def test_sentence_streaming_ask_user_event() -> None:
    session = FakeSession(language_code="hi-IN")
    tts = SleepingFakeTTS(delay=0.01)
    pipeline = VoicePipeline(session=session, stt=FakeSTT(), tts=tts)
    queue = session.bus.subscribe()

    question = (
        "कृपया अपना पैन कार्ड नंबर बताएं? "
        "हम इसे तुरंत पोर्टल पर सत्यापित करेंगे।"
    )

    await pipeline.start()
    try:
        session.bus.emit(
            "ask_user",
            question=question,
            question_id="q_pan",
            language_code="hi-IN",
        )

        tts_events: list[AgentEvent] = []
        for _ in range(10):
            ev = await asyncio.wait_for(queue.get(), timeout=2.0)
            if ev.type == "tts":
                tts_events.append(ev)
                if len(tts_events) == 2:
                    break

        assert len(tts_events) == 2
        assert tts_events[0].data["seq"] == 0
        assert tts_events[0].data["final"] is False
        assert tts_events[1].data["seq"] == 1
        assert tts_events[1].data["final"] is True
    finally:
        await pipeline.stop()


@pytest.mark.asyncio
async def test_speak_full_text_single_wav_for_cli() -> None:
    session = FakeSession()
    tts = FakeTTS(audio=b"RIFF_cli_single_full_wav")
    pipeline = VoicePipeline(session=session, stt=FakeSTT(), tts=tts)

    text = "पहला वाक्य पच्चीस अक्षरों से अधिक लंबा है। दूसरा वाक्य भी पच्चीस अक्षरों से अधिक लंबा है।"
    wav = await pipeline.speak(text, "hi-IN")

    assert wav == b"RIFF_cli_single_full_wav"
    # speak() must synthesize the full text at once, not sentence-split
    assert len(tts.calls) == 1
    assert "पहला वाक्य" in tts.calls[0]["text"]
    assert "दूसरा वाक्य" in tts.calls[0]["text"]

