"""Unit and live tests for Sarvam Speech-to-Text client."""

from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
import respx

from agastya.voice.stt import SARVAM_STT_URL, SarvamSTT
from agastya.voice.types import SarvamError, Transcript


@pytest.mark.asyncio
async def test_transcribe_success_parse():
    """Verify successful response parsing with text, language, and raw payload."""
    expected_payload = {
        "request_id": "req-12345",
        "transcript": "नमस्ते दुनिया",
        "language_code": "hi-IN",
    }
    with respx.mock(base_url="https://api.sarvam.ai") as respx_mock:
        respx_mock.post("/speech-to-text").respond(status_code=200, json=expected_payload)

        stt = SarvamSTT(api_key="dummy-key")
        result = await stt.transcribe(b"RIFFdummydata", filename="test.wav", language_code="hi-IN")

        assert isinstance(result, Transcript)
        assert result.text == "नमस्ते दुनिया"
        assert result.language_code == "hi-IN"
        assert result.raw == expected_payload


@pytest.mark.asyncio
async def test_transcribe_auto_detect_language():
    """Verify that language_code 'unknown' auto-detects and uses response language."""
    payload = {
        "request_id": "req-autodetect",
        "transcript": "Hello world",
        "language_code": "en-IN",
        "language_probability": 0.98,
    }
    with respx.mock(base_url="https://api.sarvam.ai") as respx_mock:
        respx_mock.post("/speech-to-text").respond(status_code=200, json=payload)

        stt = SarvamSTT(api_key="dummy-key")
        result = await stt.transcribe(b"RIFFdummydata", language_code="unknown")

        assert result.text == "Hello world"
        assert result.language_code == "en-IN"


@pytest.mark.asyncio
async def test_transcribe_auto_detect_fallback():
    """Verify fallback to requested language if response language_code is null/missing."""
    payload = {
        "request_id": "req-fallback",
        "transcript": "Some text",
        "language_code": None,
    }
    with respx.mock(base_url="https://api.sarvam.ai") as respx_mock:
        respx_mock.post("/speech-to-text").respond(status_code=200, json=payload)

        stt = SarvamSTT(api_key="dummy-key")
        result = await stt.transcribe(b"RIFFdummydata", language_code="unknown")

        assert result.text == "Some text"
        assert result.language_code == "unknown"


@pytest.mark.asyncio
async def test_transcribe_missing_key(monkeypatch):
    """Verify SarvamError is raised when SARVAM_API_KEY is not set."""
    # Ensure neither explicit key nor settings key is available
    mock_settings = MagicMock()
    mock_settings.SARVAM_API_KEY = None
    monkeypatch.setattr("agastya.voice.stt.get_settings", lambda: mock_settings)

    stt = SarvamSTT(api_key=None)
    with pytest.raises(SarvamError, match="SARVAM_API_KEY not set") as exc_info:
        await stt.transcribe(b"RIFFdummydata")

    assert exc_info.value.status_code is None


@pytest.mark.asyncio
async def test_transcribe_400_error():
    """Verify 400 Bad Request raises SarvamError without retry."""
    with respx.mock(base_url="https://api.sarvam.ai") as respx_mock:
        route = respx_mock.post("/speech-to-text").respond(
            status_code=400,
            text='{"error": "Invalid audio file format"}',
        )

        stt = SarvamSTT(api_key="dummy-key")
        with pytest.raises(SarvamError) as exc_info:
            await stt.transcribe(b"corrupted-bytes")

        assert exc_info.value.status_code == 400
        assert "Invalid audio file format" in str(exc_info.value)
        # Should not retry on 400
        assert route.call_count == 1


@pytest.mark.asyncio
async def test_transcribe_503_then_200_retry():
    """Verify 503 error is retried once after delay and succeeds on 200."""
    with respx.mock(base_url="https://api.sarvam.ai") as respx_mock:
        route = respx_mock.post("/speech-to-text").mock(
            side_effect=[
                httpx.Response(503, text="Service Unavailable"),
                httpx.Response(200, json={"transcript": "success after retry", "language_code": "hi-IN"}),
            ]
        )

        stt = SarvamSTT(api_key="dummy-key")
        stt._retry_delay = 0.01  # fast retry for test

        result = await stt.transcribe(b"audio-data")

        assert route.call_count == 2
        assert result.text == "success after retry"
        assert result.language_code == "hi-IN"


@pytest.mark.asyncio
async def test_transcribe_429_then_200_retry():
    """Verify 429 Too Many Requests is retried once after delay."""
    with respx.mock(base_url="https://api.sarvam.ai") as respx_mock:
        route = respx_mock.post("/speech-to-text").mock(
            side_effect=[
                httpx.Response(429, text="Rate limit exceeded"),
                httpx.Response(200, json={"transcript": "rate limit passed", "language_code": "te-IN"}),
            ]
        )

        stt = SarvamSTT(api_key="dummy-key")
        stt._retry_delay = 0.01

        result = await stt.transcribe(b"audio-data", language_code="te-IN")

        assert route.call_count == 2
        assert result.text == "rate limit passed"
        assert result.language_code == "te-IN"


@pytest.mark.asyncio
async def test_transcribe_503_retry_exhausted():
    """Verify SarvamError is raised with 503 status when retry also fails."""
    with respx.mock(base_url="https://api.sarvam.ai") as respx_mock:
        route = respx_mock.post("/speech-to-text").mock(
            side_effect=[
                httpx.Response(503, text="Gateway Timeout First"),
                httpx.Response(503, text="Gateway Timeout Second"),
            ]
        )

        stt = SarvamSTT(api_key="dummy-key")
        stt._retry_delay = 0.01

        with pytest.raises(SarvamError) as exc_info:
            await stt.transcribe(b"audio-data")

        assert exc_info.value.status_code == 503
        assert "Gateway Timeout Second" in str(exc_info.value)
        assert route.call_count == 2


@pytest.mark.asyncio
async def test_transcribe_multipart_contains_file_model_language():
    """Verify multipart payload contains file, model, and language_code fields."""
    with respx.mock(base_url="https://api.sarvam.ai") as respx_mock:
        route = respx_mock.post("/speech-to-text").respond(
            status_code=200,
            json={"transcript": "ok", "language_code": "ta-IN"},
        )

        audio_bytes = b"SAMPLE_AUDIO_BYTES_12345"
        stt = SarvamSTT(api_key="dummy-key", model="saarika:v2.5")
        await stt.transcribe(audio_bytes, filename="speech.wav", language_code="ta-IN")

        assert route.call_count == 1
        req = route.calls.last.request

        content_type = req.headers.get("content-type", "")
        assert "multipart/form-data" in content_type

        body = req.content
        assert b'name="file"; filename="speech.wav"' in body
        assert audio_bytes in body
        assert b'name="model"' in body
        assert b"saarika:v2.5" in body
        assert b'name="language_code"' in body
        assert b"ta-IN" in body


@pytest.mark.asyncio
async def test_transcribe_header_api_subscription_key_sent():
    """Verify api-subscription-key header is sent with request."""
    with respx.mock(base_url="https://api.sarvam.ai") as respx_mock:
        route = respx_mock.post("/speech-to-text").respond(
            status_code=200,
            json={"transcript": "ok", "language_code": "hi-IN"},
        )

        custom_key = "sub-key-xyz-789"
        stt = SarvamSTT(api_key=custom_key)
        await stt.transcribe(b"audio", filename="audio.wav")

        assert route.call_count == 1
        sent_header = route.calls.last.request.headers.get("api-subscription-key")
        assert sent_header == custom_key


@pytest.mark.asyncio
async def test_transcribe_with_custom_client_not_closed():
    """Verify an external client passed to SarvamSTT is used and not closed."""
    with respx.mock(base_url="https://api.sarvam.ai") as respx_mock:
        respx_mock.post("/speech-to-text").respond(
            status_code=200,
            json={"transcript": "done", "language_code": "hi-IN"},
        )

        async with httpx.AsyncClient() as external_client:
            stt = SarvamSTT(api_key="dummy-key", client=external_client)
            await stt.transcribe(b"audio")
            assert not external_client.is_closed


@pytest.mark.asyncio
async def test_transcribe_invalid_json_response():
    """Verify SarvamError is raised when response is not valid JSON."""
    with respx.mock(base_url="https://api.sarvam.ai") as respx_mock:
        respx_mock.post("/speech-to-text").respond(
            status_code=200,
            text="<html>Not JSON</html>",
        )

        stt = SarvamSTT(api_key="dummy-key")
        with pytest.raises(SarvamError, match="Failed to parse Sarvam response JSON"):
            await stt.transcribe(b"audio")


@pytest.mark.live
@pytest.mark.asyncio
@pytest.mark.skipif(
    not Path("tests/fixtures/audio/sample_hi.wav").exists(),
    reason="sample_hi.wav fixture missing",
)
async def test_transcribe_live():
    """Live test transcribing real sample_hi.wav if available."""
    sample_path = Path("tests/fixtures/audio/sample_hi.wav")
    audio_bytes = sample_path.read_bytes()

    stt = SarvamSTT()
    result = await stt.transcribe(audio_bytes, filename="sample_hi.wav")

    assert isinstance(result, Transcript)
    assert len(result.text.strip()) > 0
    assert result.language_code
