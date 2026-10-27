"""Tests for Sarvam text-to-speech and audio utilities."""

import base64
import io
from pathlib import Path
import wave

import httpx
import pytest
import respx

from agastya.config import Settings
from agastya.voice.tts import SarvamTTS
from agastya.voice.types import SarvamError
from agastya.voice.wav import concat_wavs, split_text


def make_mock_wav(
    nframes: int,
    framerate: int = 22050,
    nchannels: int = 1,
    sampwidth: int = 2,
) -> bytes:
    """Create in-memory PCM WAV bytes for testing."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(nchannels)
        w.setsampwidth(sampwidth)
        w.setframerate(framerate)
        w.writeframes(b"\x01\x00" * (nframes * nchannels))
    return buf.getvalue()


# ============================================================================
# Unit tests: agastya.voice.wav
# ============================================================================


def test_concat_wavs_empty():
    assert concat_wavs([]) == b""


def test_concat_wavs_single():
    wav = make_mock_wav(100)
    result = concat_wavs([wav])
    assert result == wav
    with wave.open(io.BytesIO(result), "rb") as r:
        assert r.getnframes() == 100


def test_concat_wavs_multi():
    w1 = make_mock_wav(120)
    w2 = make_mock_wav(230)
    w3 = make_mock_wav(50)
    result = concat_wavs([w1, w2, w3])

    with wave.open(io.BytesIO(result), "rb") as r:
        assert r.getnframes() == 120 + 230 + 50
        assert r.getframerate() == 22050
        assert r.getnchannels() == 1
        assert r.getsampwidth() == 2


def test_concat_wavs_parameter_mismatch():
    w1 = make_mock_wav(100, framerate=22050)
    w2 = make_mock_wav(100, framerate=16000)
    with pytest.raises(ValueError, match="WAV parameters at index 1 do not match"):
        concat_wavs([w1, w2])

    w3 = make_mock_wav(100, nchannels=2)
    with pytest.raises(ValueError, match="WAV parameters at index 1 do not match"):
        concat_wavs([w1, w3])

    w4 = make_mock_wav(100, sampwidth=1)
    with pytest.raises(ValueError, match="WAV parameters at index 1 do not match"):
        concat_wavs([w1, w4])


def test_split_text_empty_and_whitespace():
    assert split_text("", 100) == []
    assert split_text("   \n\t  ", 100) == []
    with pytest.raises(ValueError, match="max_chars must be positive"):
        split_text("test", 0)


def test_split_text_single_short():
    assert split_text("नमस्ते", 50) == ["नमस्ते"]


def test_split_text_hindi_danda_splitting():
    text = "यह पहला वाक्य है। यह दूसरा वाक्य है।"
    chunks = split_text(text, max_chars=25)
    assert len(chunks) == 2
    assert chunks[0] == "यह पहला वाक्य है।"
    assert chunks[1] == "यह दूसरा वाक्य है।"


def test_split_text_various_delimiters():
    text = "नमस्ते! आप कैसे हैं? मैं ठीक हूँ। सब अच्छा है."
    chunks = split_text(text, max_chars=15)
    assert len(chunks) == 4
    assert chunks == ["नमस्ते!", "आप कैसे हैं?", "मैं ठीक हूँ।", "सब अच्छा है."]


def test_split_text_sentence_too_long_splits_on_spaces():
    text = "यह एक बहुत लंबा वाक्य है बिना किसी विराम चिह्न के"
    chunks = split_text(text, max_chars=18)
    for c in chunks:
        assert len(c) <= 18
        assert c.strip() == c
        assert len(c) > 0
    assert " ".join(chunks) == text


def test_split_text_long_word_fallback():
    word = "a" * 25
    chunks = split_text(word, max_chars=10)
    assert chunks == ["a" * 10, "a" * 10, "a" * 5]


# ============================================================================
# Unit tests: SarvamTTS (Mocked via respx, no network)
# ============================================================================


@pytest.mark.asyncio
@respx.mock
async def test_tts_single_chunk():
    wav_bytes = make_mock_wav(150)
    b64_audio = base64.b64encode(wav_bytes).decode()

    respx.post("https://api.sarvam.ai/text-to-speech").respond(
        status_code=200,
        json={"request_id": "test-req", "audios": [b64_audio]},
    )

    tts = SarvamTTS(api_key="mock-key")
    result = await tts.synthesize("नमस्ते", language_code="hi-IN")

    with wave.open(io.BytesIO(result), "rb") as r:
        assert r.getnframes() == 150
        assert r.getframerate() == 22050


@pytest.mark.asyncio
@respx.mock
async def test_tts_multi_chunk_concatenation_frame_sum():
    wav1 = make_mock_wav(100)
    wav2 = make_mock_wav(250)
    b64_1 = base64.b64encode(wav1).decode()
    b64_2 = base64.b64encode(wav2).decode()

    route = respx.post("https://api.sarvam.ai/text-to-speech")
    route.side_effect = [
        httpx.Response(200, json={"audios": [b64_1]}),
        httpx.Response(200, json={"audios": [b64_2]}),
    ]

    tts = SarvamTTS(api_key="mock-key", max_chars=25)
    result = await tts.synthesize(
        "यह पहला वाक्य है। यह दूसरा वाक्य है।",
        language_code="hi-IN",
    )

    assert route.call_count == 2
    with wave.open(io.BytesIO(result), "rb") as r:
        assert r.getnframes() == 100 + 250 == 350


@pytest.mark.asyncio
@respx.mock
async def test_tts_request_json_fields_and_headers():
    wav = make_mock_wav(50)
    b64 = base64.b64encode(wav).decode()

    route = respx.post("https://api.sarvam.ai/text-to-speech").respond(
        status_code=200,
        json={"audios": [b64]},
    )

    tts = SarvamTTS(
        api_key="custom-key-xyz",
        model="bulbul:v2",
        speaker="anushka",
    )
    await tts.synthesize("परीक्षण संदेश।", language_code="hi-IN")

    assert route.call_count == 1
    req = route.calls.last.request
    assert req.headers["api-subscription-key"] == "custom-key-xyz"
    assert req.headers["content-type"] == "application/json"

    import json
    data = json.loads(req.content.decode())
    assert data["text"] == "परीक्षण संदेश।"
    assert data["language_code"] == "hi-IN"
    assert data["model"] == "bulbul:v2"
    assert data["speaker"] == "anushka"


@pytest.mark.asyncio
async def test_tts_missing_api_key(monkeypatch):
    monkeypatch.setattr(
        "agastya.voice.tts.get_settings",
        lambda: Settings(_env_file=None, SARVAM_API_KEY=None),
    )
    tts = SarvamTTS(api_key=None)
    with pytest.raises(SarvamError, match="SARVAM_API_KEY not set"):
        await tts.synthesize("परीक्षण", language_code="hi-IN")


@pytest.mark.asyncio
async def test_tts_empty_text_returns_empty_bytes():
    tts = SarvamTTS(api_key="dummy")
    assert await tts.synthesize("", "hi-IN") == b""
    assert await tts.synthesize("   ", "hi-IN") == b""


@pytest.mark.asyncio
@respx.mock
async def test_tts_400_error_path():
    respx.post("https://api.sarvam.ai/text-to-speech").respond(
        status_code=400,
        text='{"error":{"message":"Invalid speaker"}}',
    )

    tts = SarvamTTS(api_key="mock-key")
    with pytest.raises(SarvamError) as exc_info:
        await tts.synthesize("परीक्षण", language_code="hi-IN")

    assert exc_info.value.status_code == 400
    assert "Invalid speaker" in str(exc_info.value)


@pytest.mark.asyncio
@respx.mock
async def test_tts_retry_on_429_success():
    wav = make_mock_wav(80)
    b64 = base64.b64encode(wav).decode()

    route = respx.post("https://api.sarvam.ai/text-to-speech")
    route.side_effect = [
        httpx.Response(429, text="Rate limit exceeded"),
        httpx.Response(200, json={"audios": [b64]}),
    ]

    tts = SarvamTTS(api_key="mock-key")
    tts.retry_delay = 0.01

    res = await tts.synthesize("परीक्षण", language_code="hi-IN")
    assert route.call_count == 2
    with wave.open(io.BytesIO(res), "rb") as r:
        assert r.getnframes() == 80


@pytest.mark.asyncio
@respx.mock
async def test_tts_retry_on_503_success():
    wav = make_mock_wav(90)
    b64 = base64.b64encode(wav).decode()

    route = respx.post("https://api.sarvam.ai/text-to-speech")
    route.side_effect = [
        httpx.Response(503, text="Service unavailable"),
        httpx.Response(200, json={"audios": [b64]}),
    ]

    tts = SarvamTTS(api_key="mock-key")
    tts.retry_delay = 0.01

    res = await tts.synthesize("परीक्षण", language_code="hi-IN")
    assert route.call_count == 2
    with wave.open(io.BytesIO(res), "rb") as r:
        assert r.getnframes() == 90


@pytest.mark.asyncio
@respx.mock
async def test_tts_retry_on_500_failure():
    route = respx.post("https://api.sarvam.ai/text-to-speech")
    route.side_effect = [
        httpx.Response(500, text="Internal server error"),
        httpx.Response(500, text="Internal server error retry"),
    ]

    tts = SarvamTTS(api_key="mock-key")
    tts.retry_delay = 0.01

    with pytest.raises(SarvamError) as exc_info:
        await tts.synthesize("परीक्षण", language_code="hi-IN")

    assert route.call_count == 2
    assert exc_info.value.status_code == 500


@pytest.mark.asyncio
@respx.mock
async def test_tts_invalid_response_missing_audios():
    respx.post("https://api.sarvam.ai/text-to-speech").respond(
        status_code=200,
        json={"unexpected_key": []},
    )

    tts = SarvamTTS(api_key="mock-key")
    with pytest.raises(SarvamError, match="missing 'audios'"):
        await tts.synthesize("परीक्षण", language_code="hi-IN")


@pytest.mark.asyncio
@respx.mock
async def test_tts_custom_client_not_closed():
    wav = make_mock_wav(60)
    b64 = base64.b64encode(wav).decode()

    respx.post("https://api.sarvam.ai/text-to-speech").respond(
        status_code=200,
        json={"audios": [b64]},
    )

    async with httpx.AsyncClient() as custom_client:
        tts = SarvamTTS(api_key="mock-key", client=custom_client)
        await tts.synthesize("परीक्षण", language_code="hi-IN")
        assert not custom_client.is_closed


# ============================================================================
# Live test (@pytest.mark.live)
# ============================================================================


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_synthesize_and_save_fixture():
    """Live TTS test against Sarvam API.

    Synthesizes 'नमस्ते, मैं अगस्त्य हूँ।' in hi-IN and saves the result
    to tests/fixtures/audio/sample_hi.wav for other sessions to use.
    """
    tts = SarvamTTS(model="bulbul:v3", speaker="shubh")
    wav_bytes = await tts.synthesize("नमस्ते, मैं अगस्त्य हूँ।", language_code="hi-IN")

    assert len(wav_bytes) > 0

    with wave.open(io.BytesIO(wav_bytes), "rb") as r:
        assert r.getnframes() > 0
        assert r.getframerate() in (8000, 16000, 22050, 24000, 32000, 44100, 48000)
        assert r.getnchannels() in (1, 2)

    fixture_path = Path("tests/fixtures/audio/sample_hi.wav")
    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    fixture_path.write_bytes(wav_bytes)

    assert fixture_path.exists()
    assert fixture_path.stat().st_size > 0
