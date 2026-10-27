"""Tests for Agastya Assist CLI and Audio I/O."""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
import struct
from unittest.mock import AsyncMock, MagicMock, patch
import wave

import pytest

from agastya.agent.events import AgentEvent
from agastya.cli import (
    CYAN,
    DIM,
    RED,
    YELLOW,
    RESET,
    build_parser,
    cmd_profile_delete,
    cmd_profile_set,
    cmd_profile_show,
    cmd_serve,
    format_event,
    main,
    run_chat,
)
from agastya.config import get_settings
from agastya.profile.fields import FIELDS
from agastya.profile.store import ProfileStore
from agastya.voice.audio_io import fix_wav_header, play_wav, record_until_enter


# ---------------------------------------------------------------------------
# 1. Argparse Wiring Tests
# ---------------------------------------------------------------------------


def test_argparse_chat_defaults() -> None:
    parser = build_parser()
    args = parser.parse_args(["chat"])
    assert args.command == "chat"
    assert args.user == "default"
    assert args.lang == get_settings().DEFAULT_LANGUAGE
    assert args.voice is False
    assert args.headed is False
    assert args.verbose is False


def test_argparse_chat_custom_flags() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "chat",
            "--user",
            "citizen1",
            "--lang",
            "ta-IN",
            "--voice",
            "--headed",
            "--verbose",
        ]
    )
    assert args.command == "chat"
    assert args.user == "citizen1"
    assert args.lang == "ta-IN"
    assert args.voice is True
    assert args.headed is True
    assert args.verbose is True


def test_argparse_profile_show() -> None:
    parser = build_parser()
    args = parser.parse_args(["profile", "show"])
    assert args.command == "profile"
    assert args.profile_action == "show"
    assert args.user == "default"

    args_custom = parser.parse_args(["profile", "show", "--user", "user42"])
    assert args_custom.user == "user42"


def test_argparse_profile_set() -> None:
    parser = build_parser()
    args = parser.parse_args(["profile", "set", "pan", "ABCDE1234F"])
    assert args.command == "profile"
    assert args.profile_action == "set"
    assert args.field == "pan"
    assert args.value == "ABCDE1234F"
    assert args.user == "default"

    args_custom = parser.parse_args(
        ["profile", "set", "aadhaar", "123456789012", "--user", "user42"]
    )
    assert args_custom.field == "aadhaar"
    assert args_custom.value == "123456789012"
    assert args_custom.user == "user42"


def test_argparse_profile_delete() -> None:
    parser = build_parser()
    args = parser.parse_args(["profile", "delete", "mobile"])
    assert args.command == "profile"
    assert args.profile_action == "delete"
    assert args.field == "mobile"
    assert args.user == "default"


def test_argparse_serve_defaults() -> None:
    parser = build_parser()
    args = parser.parse_args(["serve"])
    assert args.command == "serve"
    assert args.host == "127.0.0.1"
    assert args.port == 8000
    assert args.reload is False


def test_argparse_serve_custom() -> None:
    parser = build_parser()
    args = parser.parse_args(
        ["serve", "--host", "0.0.0.0", "--port", "9090", "--reload"]
    )
    assert args.command == "serve"
    assert args.host == "0.0.0.0"
    assert args.port == 9090
    assert args.reload is True


def test_argparse_invalid_subcommand() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["invalid_subcommand"])


def test_argparse_profile_missing_action() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["profile"])


# ---------------------------------------------------------------------------
# 2. Profile set / show / delete against tmp DATA_DIR
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def test_profile_set_valid(tmp_env: Path, capsys: pytest.CaptureFixture) -> None:
    parser = build_parser()
    args = parser.parse_args(["profile", "set", "pan", "abcde1234f", "--user", "u1"])
    ret = cmd_profile_set(args)
    assert ret == 0

    out, err = capsys.readouterr()
    assert "Set pan = XXXXX1234X" in out

    with ProfileStore(user_id="u1") as store:
        assert store.get("pan") == "ABCDE1234F"
        assert store.masked("pan") == "XXXXX1234X"


def test_profile_set_invalid_validation(tmp_env: Path, capsys: pytest.CaptureFixture) -> None:
    parser = build_parser()
    args = parser.parse_args(["profile", "set", "pan", "not-a-pan", "--user", "u1"])
    ret = cmd_profile_set(args)
    assert ret == 2

    out, err = capsys.readouterr()
    assert "Validation error" in err

    with ProfileStore(user_id="u1") as store:
        assert store.get("pan") is None


def test_profile_set_unknown_field(tmp_env: Path, capsys: pytest.CaptureFixture) -> None:
    parser = build_parser()
    args = parser.parse_args(["profile", "set", "unknown_field", "value", "--user", "u1"])
    ret = cmd_profile_set(args)
    assert ret == 2

    out, err = capsys.readouterr()
    assert "Unknown field" in err


def test_profile_show_table(tmp_env: Path, capsys: pytest.CaptureFixture) -> None:
    with ProfileStore(user_id="u_show") as store:
        store.set("full_name", "Citizen Sharma")
        store.set("pan", "ABCDE1234F")
        store.set("aadhaar", "123456789012")

    parser = build_parser()
    args = parser.parse_args(["profile", "show", "--user", "u_show"])
    ret = cmd_profile_show(args)
    assert ret == 0

    out, err = capsys.readouterr()
    assert "Field" in out
    assert "Value" in out
    assert "Label" in out

    # Verify all catalog fields are printed
    for f in FIELDS:
        assert f in out

    # Verify sensitive fields are properly masked in the table
    assert "XXXXX1234X" in out
    assert "ABCDE1234F" not in out
    assert "XXXXXXXX9012" in out
    assert "123456789012" not in out
    assert "Citizen Sharma" in out


def test_profile_delete(tmp_env: Path, capsys: pytest.CaptureFixture) -> None:
    with ProfileStore(user_id="u_del") as store:
        store.set("mobile", "9876543210")
        assert store.get("mobile") == "9876543210"

    parser = build_parser()
    args = parser.parse_args(["profile", "delete", "mobile", "--user", "u_del"])
    ret = cmd_profile_delete(args)
    assert ret == 0

    out, _ = capsys.readouterr()
    assert "Deleted mobile" in out

    with ProfileStore(user_id="u_del") as store:
        assert store.get("mobile") is None

    # Delete again when not present
    ret2 = cmd_profile_delete(args)
    assert ret2 == 0
    out2, _ = capsys.readouterr()
    assert "was not set" in out2


def test_main_dispatch_profile(tmp_env: Path, capsys: pytest.CaptureFixture) -> None:
    assert main(["profile", "set", "pincode", "110001"]) == 0
    assert main(["profile", "show"]) == 0
    out, _ = capsys.readouterr()
    assert "110001" in out
    assert main(["profile", "delete", "pincode"]) == 0


# ---------------------------------------------------------------------------
# 3. WAV Header Fixer on Synthetic Bytes
# ---------------------------------------------------------------------------


def _make_synthetic_wav(pcm_bytes: bytes, riff_size: int = 0x7FFFFFFF, data_size: int = 0x7FFFFFFF) -> bytes:
    """Create a synthetic 16-bit 16kHz mono WAV byte string."""
    channels = 1
    sample_rate = 16000
    bits_per_sample = 16
    byte_rate = sample_rate * channels * (bits_per_sample // 8)
    block_align = channels * (bits_per_sample // 8)

    fmt_chunk = (
        b"fmt "
        + struct.pack("<I", 16)
        + struct.pack("<H", 1)  # PCM
        + struct.pack("<H", channels)
        + struct.pack("<I", sample_rate)
        + struct.pack("<I", byte_rate)
        + struct.pack("<H", block_align)
        + struct.pack("<H", bits_per_sample)
    )

    header = (
        b"RIFF"
        + struct.pack("<I", riff_size)
        + b"WAVE"
        + fmt_chunk
        + b"data"
        + struct.pack("<I", data_size)
    )
    return header + pcm_bytes


def test_fix_wav_header_too_short() -> None:
    short_data = b"RIFF1234WAVE"
    assert fix_wav_header(short_data) == short_data


def test_fix_wav_header_non_wav() -> None:
    non_wav = b"0" * 60
    assert fix_wav_header(non_wav) == non_wav


def test_fix_wav_header_synthetic_streaming_arecord() -> None:
    # 16-bit mono 16kHz = 2 bytes per sample. 200 samples = 400 bytes
    pcm_payload = b"\x01\x00" * 200
    synthetic = _make_synthetic_wav(pcm_payload, riff_size=0x7FFFFFFF, data_size=0x7FFFFFFF)
    total_len = len(synthetic)

    fixed = fix_wav_header(synthetic)
    assert len(fixed) == total_len

    # RIFF size at bytes 4..8 should be total_len - 8
    fixed_riff_size = struct.unpack_from("<I", fixed, 4)[0]
    assert fixed_riff_size == total_len - 8

    # data chunk size at bytes 40..44 should be 400
    fixed_data_size = struct.unpack_from("<I", fixed, 40)[0]
    assert fixed_data_size == len(pcm_payload)

    # Must open cleanly with Python's wave module
    with wave.open(io.BytesIO(fixed), "rb") as r:
        assert r.getnchannels() == 1
        assert r.getframerate() == 16000
        assert r.getsampwidth() == 2
        assert r.getnframes() == 200


def test_fix_wav_header_zero_data() -> None:
    synthetic = _make_synthetic_wav(b"", riff_size=0x7FFFFFFF, data_size=0x7FFFFFFF)
    fixed = fix_wav_header(synthetic)

    with wave.open(io.BytesIO(fixed), "rb") as r:
        assert r.getnframes() == 0


def test_fix_wav_header_multi_chunk() -> None:
    # Header with an extra subchunk before data
    extra_chunk = b"junk" + struct.pack("<I", 8) + b"12345678"
    pcm_payload = b"\x00\x00" * 50

    channels = 1
    sample_rate = 16000
    bits_per_sample = 16
    byte_rate = sample_rate * channels * (bits_per_sample // 8)
    block_align = channels * (bits_per_sample // 8)
    fmt_chunk = (
        b"fmt "
        + struct.pack("<I", 16)
        + struct.pack("<H", 1)
        + struct.pack("<H", channels)
        + struct.pack("<I", sample_rate)
        + struct.pack("<I", byte_rate)
        + struct.pack("<H", block_align)
        + struct.pack("<H", bits_per_sample)
    )

    header = (
        b"RIFF"
        + struct.pack("<I", 0x7FFFFFFF)
        + b"WAVE"
        + fmt_chunk
        + extra_chunk
        + b"data"
        + struct.pack("<I", 0x7FFFFFFF)
    )
    raw = header + pcm_payload
    fixed = fix_wav_header(raw)

    fixed_riff = struct.unpack_from("<I", fixed, 4)[0]
    assert fixed_riff == len(raw) - 8

    # Verify data chunk size
    data_pos = raw.find(b"data")
    fixed_data = struct.unpack_from("<I", fixed, data_pos + 4)[0]
    assert fixed_data == len(pcm_payload)


# ---------------------------------------------------------------------------
# 4. Event Printer Formatting Function format_event() Tests
# ---------------------------------------------------------------------------


def test_format_event_assistant_text() -> None:
    event = AgentEvent(type="assistant_text", data={"text": "Hello, how can I help?"})
    formatted = format_event(event)
    assert formatted is not None
    assert "Agastya: Hello, how can I help?" in formatted
    assert CYAN in formatted
    assert RESET in formatted


def test_format_event_ask_user() -> None:
    event = AgentEvent(type="ask_user", data={"question": "Please enter your Aadhaar number."})
    formatted = format_event(event)
    assert formatted is not None
    assert formatted == "❓ Please enter your Aadhaar number."


def test_format_event_blocked() -> None:
    event = AgentEvent(type="blocked", data={"reason": "Payment requires citizen authorization"})
    formatted = format_event(event)
    assert formatted is not None
    assert "⚠ Your turn: Payment requires citizen authorization" in formatted
    assert YELLOW in formatted
    assert RESET in formatted


def test_format_event_handoff() -> None:
    event = AgentEvent(type="handoff", data={"reason": "Login credentials needed"})
    formatted = format_event(event)
    assert formatted is not None
    assert "⚠ Your turn: Login credentials needed" in formatted
    assert YELLOW in formatted


def test_format_event_status_handoff() -> None:
    event = AgentEvent(type="status", data={"handoff": True, "reason": "OTP entry required"})
    formatted = format_event(event)
    assert formatted is not None
    assert "⚠ Your turn: OTP entry required" in formatted
    assert YELLOW in formatted


def test_format_event_status_non_handoff() -> None:
    event = AgentEvent(type="status", data={"status": "navigating"})
    assert format_event(event, verbose=False) is None
    verbose_fmt = format_event(event, verbose=True)
    assert verbose_fmt is not None
    assert "navigating" in verbose_fmt
    assert DIM in verbose_fmt


def test_format_event_screenshot() -> None:
    event = AgentEvent(type="screenshot", data={"url": "https://incometax.gov.in"})
    formatted = format_event(event)
    assert formatted is not None
    assert "  · page: https://incometax.gov.in" in formatted
    assert DIM in formatted
    assert RESET in formatted


def test_format_event_tool_call() -> None:
    event = AgentEvent(
        type="tool_call",
        data={"name": "mcp__agastya__browser_click", "input": {"ref": "f12"}},
    )
    # Non-verbose: must return None
    assert format_event(event, verbose=False) is None

    # Verbose: must return dim '  · browser_click(ref='f12')'
    formatted = format_event(event, verbose=True)
    assert formatted is not None
    assert "  · browser_click(ref='f12')" in formatted
    assert DIM in formatted
    assert RESET in formatted


def test_format_event_tool_call_json_string() -> None:
    event = AgentEvent(
        type="tool_call",
        data={"name": "browser_fill", "input": '{"ref": "f1", "value": "test"}'},
    )
    formatted = format_event(event, verbose=True)
    assert formatted is not None
    assert "browser_fill" in formatted
    assert "ref='f1'" in formatted
    assert "value='test'" in formatted


def test_format_event_tts() -> None:
    event = AgentEvent(type="tts", data={"audio_b64": "ABCD", "mime": "audio/wav"})
    assert format_event(event, verbose=False) is None
    assert format_event(event, verbose=True) is None


def test_format_event_error() -> None:
    event = AgentEvent(type="error", data={"error": "Something went wrong"})
    assert format_event(event, verbose=False) is None
    formatted = format_event(event, verbose=True)
    assert formatted is not None
    assert "Error: Something went wrong" in formatted
    assert RED in formatted


def test_format_event_transcript() -> None:
    event = AgentEvent(type="transcript", data={"text": "namaste"})
    assert format_event(event, verbose=False) is None
    formatted = format_event(event, verbose=True)
    assert formatted is not None
    assert "transcript: namaste" in formatted


def test_format_event_profile_updated() -> None:
    event = AgentEvent(type="profile_updated", data={"field": "pan"})
    assert format_event(event, verbose=False) is None
    formatted = format_event(event, verbose=True)
    assert formatted is not None
    assert "profile updated: pan" in formatted


def test_format_event_tool_result() -> None:
    event = AgentEvent(type="tool_result", data={"result": "success"})
    assert format_event(event, verbose=False) is None
    formatted = format_event(event, verbose=True)
    assert formatted is not None
    assert "tool_result: success" in formatted


def test_format_event_done() -> None:
    event = AgentEvent(type="done", data={})
    assert format_event(event, verbose=False) is None
    formatted = format_event(event, verbose=True)
    assert formatted is not None
    assert "done" in formatted


def test_format_event_with_dict() -> None:
    # Verify dict input works identically to AgentEvent
    event_dict = {"type": "assistant_text", "data": {"text": "Dictionary test"}}
    formatted = format_event(event_dict)
    assert formatted is not None
    assert "Agastya: Dictionary test" in formatted


# ---------------------------------------------------------------------------
# 5. Audio I/O Tests (record_until_enter and play_wav)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_play_wav_no_player_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda prog: None)
    with pytest.raises(RuntimeError, match="No audio player found"):
        await play_wav(b"some audio bytes")


@pytest.mark.asyncio
async def test_play_wav_empty() -> None:
    # Empty audio bytes should return immediately without spawning
    await play_wav(b"")


@pytest.mark.asyncio
async def test_play_wav_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda prog: "/usr/bin/paplay" if prog == "paplay" else None)

    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))
    mock_proc.returncode = 0

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)) as mock_exec:
        await play_wav(b"RIFF dummy wav")
        mock_exec.assert_called_once()
        args = mock_exec.call_args[0]
        assert args[0] == "paplay"


@pytest.mark.asyncio
async def test_record_until_enter_no_arecord_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda prog: None)
    with pytest.raises(RuntimeError, match="Audio recorder 'arecord' not found"):
        await record_until_enter()


@pytest.mark.asyncio
async def test_record_until_enter_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda prog: "/usr/bin/arecord" if prog == "arecord" else None)

    raw_synthetic = _make_synthetic_wav(b"\x00\x00" * 40, riff_size=0x7FFFFFFF, data_size=0x7FFFFFFF)

    mock_stdout = asyncio.StreamReader()
    mock_stdout.feed_data(raw_synthetic)
    mock_stdout.feed_eof()

    mock_proc = MagicMock()
    mock_proc.stdout = mock_stdout
    mock_proc.terminate = MagicMock()
    mock_proc.wait = AsyncMock(return_value=0)

    loop = asyncio.get_running_loop()
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)):
        with patch.object(loop, "run_in_executor", AsyncMock(return_value="")):
            wav_bytes = await record_until_enter()

    assert len(wav_bytes) == len(raw_synthetic)
    # Check that fixed header is valid
    with wave.open(io.BytesIO(wav_bytes), "rb") as r:
        assert r.getnframes() == 40


# ---------------------------------------------------------------------------
# 6. Serve Command Tests
# ---------------------------------------------------------------------------


def test_cmd_serve() -> None:
    parser = build_parser()
    args = parser.parse_args(["serve", "--host", "127.0.0.1", "--port", "8080", "--reload"])

    with patch("uvicorn.run") as mock_uvicorn:
        ret = cmd_serve(args)
        assert ret == 0
        mock_uvicorn.assert_called_once_with(
            "agastya.server.app:app",
            host="127.0.0.1",
            port=8080,
            reload=True,
        )


# ---------------------------------------------------------------------------
# 7. Chat Subcommand Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_chat_text_exit() -> None:
    parser = build_parser()
    args = parser.parse_args(["chat", "--user", "test_user"])

    mock_session = MagicMock()
    mock_session.start = AsyncMock()
    mock_session.stop = AsyncMock()
    mock_session.bus.subscribe = MagicMock(return_value=asyncio.Queue())
    mock_session.bus.unsubscribe = MagicMock()

    mock_pipeline = MagicMock()
    mock_pipeline.start = AsyncMock()
    mock_pipeline.stop = AsyncMock()

    loop = asyncio.get_running_loop()

    with patch("agastya.agent.session.AgentSession", return_value=mock_session), \
         patch("agastya.browser.driver.BrowserDriver", MagicMock()), \
         patch("agastya.voice.pipeline.VoicePipeline", return_value=mock_pipeline), \
         patch.object(loop, "run_in_executor", AsyncMock(side_effect=["exit"])):
        ret = await run_chat(args)
        assert ret == 0
        mock_session.start.assert_called_once()
        mock_pipeline.start.assert_called_once()
        mock_session.stop.assert_called_once()
        mock_pipeline.stop.assert_called_once()


@pytest.mark.asyncio
async def test_run_chat_voice_push_to_talk() -> None:
    parser = build_parser()
    args = parser.parse_args(["chat", "--voice", "--user", "voice_user"])

    mock_session = MagicMock()
    mock_session.start = AsyncMock()
    mock_session.stop = AsyncMock()
    mock_session.bus.subscribe = MagicMock(return_value=asyncio.Queue())
    mock_session.bus.unsubscribe = MagicMock()

    mock_pipeline = MagicMock()
    mock_pipeline.start = AsyncMock()
    mock_pipeline.stop = AsyncMock()
    mock_pipeline.handle_audio = AsyncMock()

    loop = asyncio.get_running_loop()

    # User inputs: "" (press Enter to speak), then "exit"
    with patch("agastya.agent.session.AgentSession", return_value=mock_session), \
         patch("agastya.browser.driver.BrowserDriver", MagicMock()), \
         patch("agastya.voice.pipeline.VoicePipeline", return_value=mock_pipeline), \
         patch("agastya.cli.record_until_enter", AsyncMock(return_value=b"RIFFdummy")), \
         patch.object(loop, "run_in_executor", AsyncMock(side_effect=["", "exit"])):
        ret = await run_chat(args)
        assert ret == 0
        mock_pipeline.handle_audio.assert_called_once_with(b"RIFFdummy")


@pytest.mark.asyncio
async def test_run_chat_tts_playback_in_voice_mode() -> None:
    parser = build_parser()
    args = parser.parse_args(["chat", "--voice"])

    events_q: asyncio.Queue = asyncio.Queue()
    mock_session = MagicMock()
    mock_session.start = AsyncMock()
    mock_session.stop = AsyncMock()
    mock_session.bus.subscribe = MagicMock(return_value=events_q)
    mock_session.bus.unsubscribe = MagicMock()

    mock_pipeline = MagicMock()
    mock_pipeline.start = AsyncMock()
    mock_pipeline.stop = AsyncMock()

    loop = asyncio.get_running_loop()
    import base64
    fake_wav = b"RIFFfakeaudio"
    b64_audio = base64.b64encode(fake_wav).decode("ascii")

    # Put a tts event on the queue
    events_q.put_nowait(
        AgentEvent(type="tts", data={"audio_b64": b64_audio, "mime": "audio/wav"})
    )

    async def _mock_input(*_):
        await asyncio.sleep(0.02)
        return "exit"

    with patch("agastya.agent.session.AgentSession", return_value=mock_session), \
         patch("agastya.browser.driver.BrowserDriver", MagicMock()), \
         patch("agastya.voice.pipeline.VoicePipeline", return_value=mock_pipeline), \
         patch("agastya.cli.play_wav", AsyncMock()) as mock_play, \
         patch.object(loop, "run_in_executor", side_effect=_mock_input):
        ret = await run_chat(args)
        assert ret == 0
        mock_play.assert_called_once_with(fake_wav)

