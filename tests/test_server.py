"""Tests for Agastya Assist FastAPI server and WebSocket handler."""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
import time
from typing import Any

from fastapi.testclient import TestClient
import pytest
from starlette.websockets import WebSocketDisconnect

from agastya.agent.events import EventBus
from agastya.config import get_settings
from agastya.profile.fields import FIELDS
from agastya.profile.store import ProfileStore
from agastya.server.app import create_app
from agastya.voice.types import SUPPORTED_LANGUAGES


class FakeSession:
    """Fake AgentSession for testing server WebSocket coordination."""

    def __init__(self, user_id: str = "default") -> None:
        self.user_id = user_id
        self.bus = EventBus()
        self.started = False
        self.stopped = False
        self.received_messages: list[tuple[str, str | None]] = []
        self.answered_questions: list[tuple[str, str]] = []
        self.language_code = "en-IN"
        self.should_fail_start = False

    async def start(self) -> None:
        if self.should_fail_start:
            raise RuntimeError("Claude Code not logged in")
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def send_user_message(
        self, text: str, language_code: str | None = None
    ) -> None:
        self.received_messages.append((text, language_code))

    async def answer_question(self, question_id: str, answer: str) -> None:
        self.answered_questions.append((question_id, answer))


class FakePipeline:
    """Fake VoicePipeline for testing server WebSocket coordination."""

    def __init__(self, session: FakeSession) -> None:
        self.session = session
        self.started = False
        self.stopped = False
        self.handled_audio: list[tuple[bytes, str]] = []
        self.auto_speak = True
        self.language_code = "en-IN"

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def handle_audio(
        self, audio: bytes, filename: str = "audio.wav"
    ) -> Any:
        self.handled_audio.append((audio, filename))

    def set_language(self, lang: str) -> None:
        self.language_code = lang


@pytest.fixture
def tmp_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Provide an isolated environment with a temporary DATA_DIR."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


@pytest.fixture
def client(tmp_env: Path) -> TestClient:
    """Test client using default application."""
    app = create_app()
    return TestClient(app)


# ==============================================================================
# REST API Tests
# ==============================================================================


def test_health_endpoint(client: TestClient) -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_default_factories() -> None:
    from agastya.server.app import default_session_factory, default_pipeline_factory
    sess = default_session_factory("default_user")
    assert sess.user_id == "default_user"
    pipe = default_pipeline_factory(sess)
    assert pipe.session is sess


def test_languages_endpoint(client: TestClient) -> None:
    resp = client.get("/api/languages")
    assert resp.status_code == 200
    data = resp.json()
    assert "hi-IN" in data
    assert "en-IN" in data
    assert data == SUPPORTED_LANGUAGES


def test_playbooks_endpoint(client: TestClient) -> None:
    resp = client.get("/api/playbooks")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    if data:
        pb = data[0]
        assert "id" in pb
        assert "portal" in pb
        assert "title_en" in pb
        assert "title_hi" in pb
        assert "live_url" in pb
        assert "requires_login" in pb


def test_index_route(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code in (200, 404)
    if resp.status_code == 200:
        assert "html" in resp.headers.get("content-type", "").lower()


def test_index_route_when_missing(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    # When index.html is missing, returns 404 without import/runtime crash
    orig_is_file = Path.is_file
    monkeypatch.setattr(
        Path,
        "is_file",
        lambda self: False if self.name == "index.html" else orig_is_file(self),
    )
    resp = client.get("/")
    assert resp.status_code == 404
    assert "index.html not found" in resp.json()["detail"]


def test_profile_empty_get(client: TestClient, tmp_env: Path) -> None:
    resp = client.get("/api/profile?user_id=test_empty")
    assert resp.status_code == 200
    data = resp.json()

    for field_name, info in FIELDS.items():
        assert field_name in data
        item = data[field_name]
        assert item["value"] is None
        assert item["label_en"] == info.label_en
        assert item["label_hi"] == info.label_hi
        assert item["source"] is None
        assert item["updated_at"] is None


def test_profile_put_and_masked_get(client: TestClient, tmp_env: Path) -> None:
    # 1. Update full_name (non-sensitive)
    put_resp = client.put(
        "/api/profile/full_name?user_id=alice",
        json={"value": "Alice Wonderland"},
    )
    assert put_resp.status_code == 200
    put_data = put_resp.json()
    assert put_data["ok"] is True
    assert put_data["field"] == "full_name"
    assert put_data["value"] == "Alice Wonderland"

    # 2. Update PAN (sensitive)
    pan_resp = client.put(
        "/api/profile/pan?user_id=alice",
        json={"value": "ABCDE1234F"},
    )
    assert pan_resp.status_code == 200
    pan_data = pan_resp.json()
    assert pan_data["ok"] is True
    assert pan_data["field"] == "pan"
    assert pan_data["value"] == "XXXXX1234X"

    # 3. GET profile and ensure PAN is masked while full_name is unmasked
    get_resp = client.get("/api/profile?user_id=alice")
    assert get_resp.status_code == 200
    profile = get_resp.json()
    assert profile["full_name"]["value"] == "Alice Wonderland"
    assert profile["full_name"]["source"] == "user"
    assert profile["full_name"]["updated_at"] is not None
    assert profile["pan"]["value"] == "XXXXX1234X"


def test_profile_validation_error_422(client: TestClient, tmp_env: Path) -> None:
    # Invalid PAN pattern
    resp = client.put(
        "/api/profile/pan?user_id=alice",
        json={"value": "NOT_A_PAN"},
    )
    assert resp.status_code == 422
    err_detail = resp.json()["detail"]
    assert "Invalid value for field 'pan'" in err_detail

    # Invalid Aadhaar (must be 12 digits)
    resp_aadhaar = client.put(
        "/api/profile/aadhaar?user_id=alice",
        json={"value": "12345"},
    )
    assert resp_aadhaar.status_code == 422
    assert "Invalid value for field 'aadhaar'" in resp_aadhaar.json()["detail"]

    # Invalid Pincode (must be 6 digits)
    resp_pin = client.put(
        "/api/profile/pincode?user_id=alice",
        json={"value": "123"},
    )
    assert resp_pin.status_code == 422
    assert "Invalid value for field 'pincode'" in resp_pin.json()["detail"]

    # Unknown field
    resp_unknown = client.put(
        "/api/profile/unknown_field?user_id=alice",
        json={"value": "value"},
    )
    assert resp_unknown.status_code == 422
    assert "Unknown profile field" in resp_unknown.json()["detail"]


def test_profile_delete(client: TestClient, tmp_env: Path) -> None:
    client.put("/api/profile/city?user_id=bob", json={"value": "Mumbai"})
    get1 = client.get("/api/profile?user_id=bob")
    assert get1.json()["city"]["value"] == "Mumbai"

    del_resp = client.delete("/api/profile/city?user_id=bob")
    assert del_resp.status_code == 200
    assert del_resp.json() == {"ok": True, "field": "city", "deleted": True}

    get2 = client.get("/api/profile?user_id=bob")
    assert get2.json()["city"]["value"] is None

    # Deleting again returns deleted=False
    del_resp2 = client.delete("/api/profile/city?user_id=bob")
    assert del_resp2.status_code == 200
    assert del_resp2.json()["deleted"] is False


def test_profile_multi_user_isolation(client: TestClient, tmp_env: Path) -> None:
    client.put("/api/profile/city?user_id=user1", json={"value": "Delhi"})
    client.put("/api/profile/city?user_id=user2", json={"value": "Kolkata"})

    res1 = client.get("/api/profile?user_id=user1").json()
    res2 = client.get("/api/profile?user_id=user2").json()

    assert res1["city"]["value"] == "Delhi"
    assert res2["city"]["value"] == "Kolkata"


# ==============================================================================
# WebSocket Tests
# ==============================================================================


def test_ws_text_message_and_bus_events() -> None:
    session_instance: FakeSession | None = None
    pipeline_instance: FakePipeline | None = None

    def fake_session_factory(user_id: str) -> FakeSession:
        nonlocal session_instance
        session_instance = FakeSession(user_id=user_id)
        return session_instance

    def fake_pipeline_factory(session: FakeSession) -> FakePipeline:
        nonlocal pipeline_instance
        pipeline_instance = FakePipeline(session=session)
        return pipeline_instance

    app = create_app(
        session_factory=fake_session_factory,
        pipeline_factory=fake_pipeline_factory,
    )
    test_client = TestClient(app)

    with test_client.websocket_connect("/ws?user_id=user_ws") as ws:
        assert session_instance is not None
        assert session_instance.started is True
        assert pipeline_instance is not None
        assert pipeline_instance.started is True

        # 1. Send text message from client -> session receives it
        ws.send_json({"type": "text", "text": "Namaste", "language_code": "hi-IN"})
        # Allow event loop tick for dispatch
        time.sleep(0.05)
        assert len(session_instance.received_messages) == 1
        assert session_instance.received_messages[0] == ("Namaste", "hi-IN")

        # 2. Emit an assistant_text event on fake bus -> client receives it
        session_instance.bus.emit(
            "assistant_text", text="Kaise madad karu?", language_code="hi-IN"
        )
        data = ws.receive_json()
        assert data["type"] == "assistant_text"
        assert data["data"]["text"] == "Kaise madad karu?"
        assert data["data"]["language_code"] == "hi-IN"

        # 3. Emit screenshot event on fake bus -> client receives it
        session_instance.bus.emit(
            "screenshot", png_b64="iVBORw0KGgoAAAANSUhEUg==", url="https://incometax.gov.in"
        )
        data_shot = ws.receive_json()
        assert data_shot["type"] == "screenshot"
        assert data_shot["data"]["png_b64"] == "iVBORw0KGgoAAAANSUhEUg=="

        # 4. Answer question routing
        ws.send_json({"type": "answer", "question_id": "q42", "answer": "123456"})
        time.sleep(0.05)
        assert len(session_instance.answered_questions) == 1
        assert session_instance.answered_questions[0] == ("q42", "123456")

        # 5. Set language routing
        ws.send_json({"type": "set_language", "language_code": "ta-IN"})
        time.sleep(0.05)
        assert session_instance.language_code == "ta-IN"

        # 6. Toggle TTS routing
        ws.send_json({"type": "tts", "enabled": False})
        time.sleep(0.05)
        assert pipeline_instance.auto_speak is False

        # 7. Audio message routing
        audio_payload = base64.b64encode(b"RIFFdummyWAVbytes").decode("ascii")
        ws.send_json({
            "type": "audio",
            "audio_b64": audio_payload,
            "mime": "audio/wav",
        })
        time.sleep(0.05)
        assert len(pipeline_instance.handled_audio) == 1
        assert pipeline_instance.handled_audio[0] == (b"RIFFdummyWAVbytes", "audio.wav")

    # On disconnect: session and pipeline are stopped
    assert session_instance.stopped is True
    assert pipeline_instance.stopped is True


def test_ws_invalid_json_keeps_connection_alive() -> None:
    session_instance: FakeSession | None = None

    def fake_session_factory(user_id: str) -> FakeSession:
        nonlocal session_instance
        session_instance = FakeSession(user_id=user_id)
        return session_instance

    app = create_app(
        session_factory=fake_session_factory,
        pipeline_factory=lambda s: FakePipeline(s),
    )
    test_client = TestClient(app)

    with test_client.websocket_connect("/ws") as ws:
        # Send non-JSON text
        ws.send_text("THIS IS NOT JSON {")
        err_msg = ws.receive_json()
        assert err_msg["type"] == "error"
        assert "Invalid JSON" in err_msg["data"]["error"]

        # Send message missing type
        ws.send_json({"foo": "bar"})
        err_type = ws.receive_json()
        assert err_type["type"] == "error"
        assert "Missing message type" in err_type["data"]["error"]

        # Send unknown message type
        ws.send_json({"type": "unknown_action"})
        err_unknown = ws.receive_json()
        assert err_unknown["type"] == "error"
        assert "Unknown message type" in err_unknown["data"]["error"]

        # Send valid JSON text afterwards -> should still process
        ws.send_json({"type": "text", "text": "still working", "language_code": "en-IN"})
        time.sleep(0.05)
        assert session_instance is not None
        assert len(session_instance.received_messages) == 1
        assert session_instance.received_messages[0] == ("still working", "en-IN")


def test_ws_audio_validation_errors() -> None:
    app = create_app(
        session_factory=lambda u: FakeSession(u),
        pipeline_factory=lambda s: FakePipeline(s),
    )
    test_client = TestClient(app)

    with test_client.websocket_connect("/ws") as ws:
        # Audio message without audio_b64
        ws.send_json({"type": "audio"})
        err = ws.receive_json()
        assert err["type"] == "error"
        assert "audio_b64" in err["data"]["error"]

        # Audio message with invalid base64
        ws.send_json({"type": "audio", "audio_b64": "!!!not_base64!!!"})
        err_b64 = ws.receive_json()
        assert err_b64["type"] == "error"
        assert "Invalid base64 audio" in err_b64["data"]["error"]


def test_ws_session_start_failure() -> None:
    def failing_session_factory(user_id: str) -> FakeSession:
        sess = FakeSession(user_id=user_id)
        sess.should_fail_start = True
        return sess

    app = create_app(
        session_factory=failing_session_factory,
        pipeline_factory=lambda s: FakePipeline(s),
    )
    test_client = TestClient(app)

    with test_client.websocket_connect("/ws") as ws:
        err_msg = ws.receive_json()
        assert err_msg["type"] == "error"
        assert "Claude Code not logged in" in err_msg["data"]["error"]

        # Server closes with code 1011
        with pytest.raises(WebSocketDisconnect) as exc_info:
            ws.receive_text()
        assert exc_info.value.code == 1011
