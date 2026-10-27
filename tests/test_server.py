"""Tests for Agastya Assist FastAPI server and WebSocket handler."""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
import time
from typing import Any, Callable

from fastapi.testclient import TestClient
import pytest
from starlette.websockets import WebSocketDisconnect

from agastya.agent.events import EventBus
from agastya.config import get_settings
from agastya.profile.fields import FIELDS
from agastya.profile.store import ProfileStore
from agastya.server.app import create_app
from agastya.voice.types import SUPPORTED_LANGUAGES


class FakeLiveView:
    """Fake LiveView for testing live WebSocket streaming and human control."""

    def __init__(self, browser: Any = None) -> None:
        self.browser = browser
        self.on_frame: Callable[[bytes, dict | None], Any] | None = None
        self.started = False
        self.stopped = False
        self.mouse_calls: list[tuple[str, float, float, str, float, int]] = []
        self.key_calls: list[tuple[str, str, str, str]] = []
        self.text_calls: list[str] = []
        self.nav_calls: list[str] = []

    async def start(self, on_frame: Callable[[bytes, dict | None], Any]) -> None:
        self.started = True
        self.on_frame = on_frame

    async def stop(self) -> None:
        self.stopped = True

    async def mouse(
        self,
        kind: str,
        x: float,
        y: float,
        button: str = "left",
        delta_y: float = 0.0,
        click_count: int = 1,
    ) -> None:
        self.mouse_calls.append((kind, x, y, button, delta_y, click_count))

    async def key(self, kind: str, key: str, code: str = "", text: str = "") -> None:
        self.key_calls.append((kind, key, code, text))

    async def insert_text(self, text: str) -> None:
        self.text_calls.append(text)

    async def nav(self, action: str) -> None:
        self.nav_calls.append(action)


class FakeSession:
    """Fake AgentSession for testing server WebSocket coordination."""

    def __init__(self, user_id: str = "default", browser: Any = None) -> None:
        self.user_id = user_id
        self.browser = browser if browser is not None else object()
        self.bus = EventBus()
        self.started = False
        self.stopped = False
        self.received_messages: list[tuple[str, str | None]] = []
        self.answered_questions: list[tuple[str, str]] = []
        self.language_code = "en-IN"
        self.should_fail_start = False
        self.user_in_control = False
        self.released_notes: list[str] = []

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

    async def take_control(self) -> bool:
        self.user_in_control = True
        self.bus.emit("status", status="user_control")
        return True

    async def release_control(self, note: str = "") -> bool:
        self.user_in_control = False
        self.released_notes.append(note)
        self.bus.emit("status", status="agent_control")
        return False


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
    put_resp = client.put(
        "/api/profile/full_name?user_id=alice",
        json={"value": "Alice Wonderland"},
    )
    assert put_resp.status_code == 200
    put_data = put_resp.json()
    assert put_data["ok"] is True
    assert put_data["field"] == "full_name"
    assert put_data["value"] == "Alice Wonderland"

    pan_resp = client.put(
        "/api/profile/pan?user_id=alice",
        json={"value": "ABCDE1234F"},
    )
    assert pan_resp.status_code == 200
    pan_data = pan_resp.json()
    assert pan_data["ok"] is True
    assert pan_data["field"] == "pan"
    assert pan_data["value"] == "XXXXX1234X"

    get_resp = client.get("/api/profile?user_id=alice")
    assert get_resp.status_code == 200
    profile = get_resp.json()
    assert profile["full_name"]["value"] == "Alice Wonderland"
    assert profile["full_name"]["source"] == "user"
    assert profile["full_name"]["updated_at"] is not None
    assert profile["pan"]["value"] == "XXXXX1234X"


def test_profile_validation_error_422(client: TestClient, tmp_env: Path) -> None:
    resp = client.put(
        "/api/profile/pan?user_id=alice",
        json={"value": "NOT_A_PAN"},
    )
    assert resp.status_code == 422
    err_detail = resp.json()["detail"]
    assert "Invalid value for field 'pan'" in err_detail

    resp_aadhaar = client.put(
        "/api/profile/aadhaar?user_id=alice",
        json={"value": "12345"},
    )
    assert resp_aadhaar.status_code == 422
    assert "Invalid value for field 'aadhaar'" in resp_aadhaar.json()["detail"]

    resp_pin = client.put(
        "/api/profile/pincode?user_id=alice",
        json={"value": "123"},
    )
    assert resp_pin.status_code == 422
    assert "Invalid value for field 'pincode'" in resp_pin.json()["detail"]

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
# WebSocket Tests: Session Registry & /ws
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

        ws.send_json({"type": "text", "text": "Namaste", "language_code": "hi-IN"})
        time.sleep(0.05)
        assert len(session_instance.received_messages) == 1
        assert session_instance.received_messages[0] == ("Namaste", "hi-IN")

        session_instance.bus.emit(
            "assistant_text", text="Kaise madad karu?", language_code="hi-IN"
        )
        data = ws.receive_json()
        assert data["type"] == "assistant_text"
        assert data["data"]["text"] == "Kaise madad karu?"
        assert data["data"]["language_code"] == "hi-IN"

        session_instance.bus.emit(
            "screenshot", png_b64="iVBORw0KGgoAAAANSUhEUg==", url="https://incometax.gov.in"
        )
        data_shot = ws.receive_json()
        assert data_shot["type"] == "screenshot"
        assert data_shot["data"]["png_b64"] == "iVBORw0KGgoAAAANSUhEUg=="

        ws.send_json({"type": "answer", "question_id": "q42", "answer": "123456"})
        time.sleep(0.05)
        assert len(session_instance.answered_questions) == 1
        assert session_instance.answered_questions[0] == ("q42", "123456")

        ws.send_json({"type": "set_language", "language_code": "ta-IN"})
        time.sleep(0.05)
        assert session_instance.language_code == "ta-IN"

        ws.send_json({"type": "tts", "enabled": False})
        time.sleep(0.05)
        assert pipeline_instance.auto_speak is False

        audio_payload = base64.b64encode(b"RIFFdummyWAVbytes").decode("ascii")
        ws.send_json({
            "type": "audio",
            "audio_b64": audio_payload,
            "mime": "audio/wav",
        })
        time.sleep(0.05)
        assert len(pipeline_instance.handled_audio) == 1
        assert pipeline_instance.handled_audio[0] == (b"RIFFdummyWAVbytes", "audio.wav")

    assert session_instance.stopped is True
    assert pipeline_instance.stopped is True


def test_ws_session_registry_lifecycle() -> None:
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

    assert "alice" not in app.state.sessions

    with test_client.websocket_connect("/ws?user_id=alice"):
        assert "alice" in app.state.sessions
        stored_sess, stored_pipe = app.state.sessions["alice"]
        assert stored_sess is session_instance
        assert stored_pipe is pipeline_instance

    assert "alice" not in app.state.sessions


def test_ws_session_replaced_by_second_connection() -> None:
    sessions: list[FakeSession] = []
    pipelines: list[FakePipeline] = []

    def fake_session_factory(user_id: str) -> FakeSession:
        sess = FakeSession(user_id=user_id)
        sessions.append(sess)
        return sess

    def fake_pipeline_factory(session: FakeSession) -> FakePipeline:
        pipe = FakePipeline(session=session)
        pipelines.append(pipe)
        return pipe

    app = create_app(
        session_factory=fake_session_factory,
        pipeline_factory=fake_pipeline_factory,
    )
    test_client = TestClient(app)

    with test_client.websocket_connect("/ws?user_id=bob") as ws1:
        assert len(sessions) == 1
        assert sessions[0].started is True
        assert sessions[0].stopped is False
        assert app.state.sessions["bob"][0] is sessions[0]

        with test_client.websocket_connect("/ws?user_id=bob") as ws2:
            assert len(sessions) == 2
            # First session was stopped upon replacement
            assert sessions[0].stopped is True
            assert sessions[1].started is True
            assert app.state.sessions["bob"][0] is sessions[1]

        # ws2 disconnected: removed from registry
        assert "bob" not in app.state.sessions

    # ws1 disconnected: registry remains clean
    assert "bob" not in app.state.sessions


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
        ws.send_text("THIS IS NOT JSON {")
        err_msg = ws.receive_json()
        assert err_msg["type"] == "error"
        assert "Invalid JSON" in err_msg["data"]["error"]

        ws.send_json({"foo": "bar"})
        err_type = ws.receive_json()
        assert err_type["type"] == "error"
        assert "Missing message type" in err_type["data"]["error"]

        ws.send_json({"type": "unknown_action"})
        err_unknown = ws.receive_json()
        assert err_unknown["type"] == "error"
        assert "Unknown message type" in err_unknown["data"]["error"]

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
        ws.send_json({"type": "audio"})
        err = ws.receive_json()
        assert err["type"] == "error"
        assert "audio_b64" in err["data"]["error"]

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

        with pytest.raises(WebSocketDisconnect) as exc_info:
            ws.receive_text()
        assert exc_info.value.code == 1011


# ==============================================================================
# WebSocket Tests: Live Stream & Human Control (/ws/live)
# ==============================================================================


def test_ws_live_4404_without_session() -> None:
    app = create_app()
    test_client = TestClient(app)

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with test_client.websocket_connect("/ws/live?user_id=nonexistent"):
            pass
    assert exc_info.value.code == 4404


def test_ws_live_frames_delivered_as_bytes() -> None:
    fake_live = FakeLiveView()
    session = FakeSession("charlie")
    pipeline = FakePipeline(session)

    app = create_app(
        session_factory=lambda u: session,
        pipeline_factory=lambda s: pipeline,
        live_view_factory=lambda b: fake_live,
    )
    test_client = TestClient(app)

    with test_client.websocket_connect("/ws?user_id=charlie"):
        with test_client.websocket_connect("/ws/live?user_id=charlie") as ws_live:
            assert fake_live.started is True
            assert fake_live.on_frame is not None

            # Produce a frame with metadata
            frame_data = b"\xff\xd8\xff\xe0dummy_jpeg_bytes"
            meta_data = {"width": 1280, "height": 720, "url": "https://example.com", "title": "Example"}

            loop = asyncio.new_event_loop()
            loop.run_until_complete(fake_live.on_frame(frame_data, meta_data))
            loop.close()

            # First message on meta change is JSON meta
            msg_meta = ws_live.receive_json()
            assert msg_meta["type"] == "meta"
            assert msg_meta["width"] == 1280
            assert msg_meta["height"] == 720
            assert msg_meta["url"] == "https://example.com"
            assert msg_meta["title"] == "Example"

            # Frame delivered as binary bytes
            frame_msg = ws_live.receive_bytes()
            assert isinstance(frame_msg, bytes)
            assert frame_msg == frame_data


def test_ws_live_only_latest_behaviour() -> None:
    fake_live = FakeLiveView()
    session = FakeSession("david")
    pipeline = FakePipeline(session)

    app = create_app(
        session_factory=lambda u: session,
        pipeline_factory=lambda s: pipeline,
        live_view_factory=lambda b: fake_live,
    )
    test_client = TestClient(app)

    with test_client.websocket_connect("/ws?user_id=david"):
        with test_client.websocket_connect("/ws/live?user_id=david") as ws_live:
            assert fake_live.on_frame is not None

            loop = asyncio.new_event_loop()
            async def produce_burst():
                for i in range(50):
                    await fake_live.on_frame(f"frame_{i}".encode(), None)
            loop.run_until_complete(produce_burst())
            loop.close()

            time.sleep(0.05)

            frames_received: list[bytes] = []
            while True:
                f = ws_live.receive_bytes()
                frames_received.append(f)
                if f == b"frame_49":
                    break

            assert len(frames_received) <= 5
            assert frames_received[-1] == b"frame_49"


def test_ws_live_input_rejected_before_take_control() -> None:
    fake_live = FakeLiveView()
    session = FakeSession("eve")
    pipeline = FakePipeline(session)

    app = create_app(
        session_factory=lambda u: session,
        pipeline_factory=lambda s: pipeline,
        live_view_factory=lambda b: fake_live,
    )
    test_client = TestClient(app)

    with test_client.websocket_connect("/ws?user_id=eve"):
        with test_client.websocket_connect("/ws/live?user_id=eve") as ws_live:
            # 1. Mouse rejected
            ws_live.send_json({"type": "mouse", "kind": "click", "x": 100, "y": 200})
            err1 = ws_live.receive_json()
            assert err1["type"] == "error"
            assert err1["message"] == "take control first"
            assert len(fake_live.mouse_calls) == 0

            # 2. Key rejected
            ws_live.send_json({"type": "key", "kind": "press", "key": "Enter"})
            err2 = ws_live.receive_json()
            assert err2["type"] == "error"
            assert err2["message"] == "take control first"
            assert len(fake_live.key_calls) == 0

            # 3. Text rejected
            ws_live.send_json({"type": "text", "text": "hello"})
            err3 = ws_live.receive_json()
            assert err3["type"] == "error"
            assert err3["message"] == "take control first"
            assert len(fake_live.text_calls) == 0

            # 4. Nav rejected
            ws_live.send_json({"type": "nav", "action": "back"})
            err4 = ws_live.receive_json()
            assert err4["type"] == "error"
            assert err4["message"] == "take control first"
            assert len(fake_live.nav_calls) == 0


def test_ws_live_take_control_and_forward_inputs() -> None:
    fake_live = FakeLiveView()
    session = FakeSession("frank")
    pipeline = FakePipeline(session)

    app = create_app(
        session_factory=lambda u: session,
        pipeline_factory=lambda s: pipeline,
        live_view_factory=lambda b: fake_live,
    )
    test_client = TestClient(app)

    with test_client.websocket_connect("/ws?user_id=frank"):
        with test_client.websocket_connect("/ws/live?user_id=frank") as ws_live:
            # Take control
            ws_live.send_json({"type": "control", "take": True})
            ctrl_msg = ws_live.receive_json()
            assert ctrl_msg["type"] == "control"
            assert ctrl_msg["user_in_control"] is True
            assert session.user_in_control is True

            # Mouse forwarded
            ws_live.send_json({"type": "mouse", "kind": "click", "x": 150.0, "y": 250.0, "button": "left"})
            time.sleep(0.05)
            assert len(fake_live.mouse_calls) == 1
            assert fake_live.mouse_calls[0] == ("click", 150.0, 250.0, "left", 0.0, 1)

            # Key forwarded
            ws_live.send_json({"type": "key", "kind": "press", "key": "Tab", "code": "Tab"})
            time.sleep(0.05)
            assert len(fake_live.key_calls) == 1
            assert fake_live.key_calls[0] == ("press", "Tab", "Tab", "")

            # Text forwarded
            ws_live.send_json({"type": "text", "text": "Jane Citizen"})
            time.sleep(0.05)
            assert len(fake_live.text_calls) == 1
            assert fake_live.text_calls[0] == "Jane Citizen"

            # Nav forwarded
            ws_live.send_json({"type": "nav", "action": "reload"})
            time.sleep(0.05)
            assert len(fake_live.nav_calls) == 1
            assert fake_live.nav_calls[0] == "reload"


def test_ws_live_release_control_called_with_note() -> None:
    fake_live = FakeLiveView()
    session = FakeSession("grace")
    pipeline = FakePipeline(session)

    app = create_app(
        session_factory=lambda u: session,
        pipeline_factory=lambda s: pipeline,
        live_view_factory=lambda b: fake_live,
    )
    test_client = TestClient(app)

    with test_client.websocket_connect("/ws?user_id=grace"):
        with test_client.websocket_connect("/ws/live?user_id=grace") as ws_live:
            # Take control first
            ws_live.send_json({"type": "control", "take": True})
            assert ws_live.receive_json()["user_in_control"] is True

            # Release control with note
            ws_live.send_json({
                "type": "control",
                "take": False,
                "note": "Citizen solved captcha and OTP successfully",
            })
            rel_msg = ws_live.receive_json()
            assert rel_msg["type"] == "control"
            assert rel_msg["user_in_control"] is False
            assert session.user_in_control is False
            assert session.released_notes == ["Citizen solved captcha and OTP successfully"]


def test_ws_live_control_status_forwarded_to_ws_via_bus() -> None:
    fake_live = FakeLiveView()
    session = FakeSession("heidi")
    pipeline = FakePipeline(session)

    app = create_app(
        session_factory=lambda u: session,
        pipeline_factory=lambda s: pipeline,
        live_view_factory=lambda b: fake_live,
    )
    test_client = TestClient(app)

    with test_client.websocket_connect("/ws?user_id=heidi") as ws_agent:
        with test_client.websocket_connect("/ws/live?user_id=heidi") as ws_live:
            # Take control in live socket
            ws_live.send_json({"type": "control", "take": True})
            live_ack = ws_live.receive_json()
            assert live_ack["type"] == "control"
            assert live_ack["user_in_control"] is True

            # Agent /ws receives the status event forwarded via the event bus
            agent_msg = ws_agent.receive_json()
            assert agent_msg["type"] == "status"
            assert agent_msg["data"]["status"] == "user_control"

            # Release control in live socket
            ws_live.send_json({"type": "control", "take": False, "note": "Finished"})
            live_ack2 = ws_live.receive_json()
            assert live_ack2["type"] == "control"
            assert live_ack2["user_in_control"] is False

            # Agent /ws receives release status event
            agent_msg2 = ws_agent.receive_json()
            assert agent_msg2["type"] == "status"
            assert agent_msg2["data"]["status"] == "agent_control"


def test_ws_live_disconnect_stops_live_only() -> None:
    fake_live = FakeLiveView()
    session = FakeSession("ivan")
    pipeline = FakePipeline(session)

    app = create_app(
        session_factory=lambda u: session,
        pipeline_factory=lambda s: pipeline,
        live_view_factory=lambda b: fake_live,
    )
    test_client = TestClient(app)

    with test_client.websocket_connect("/ws?user_id=ivan"):
        with test_client.websocket_connect("/ws/live?user_id=ivan"):
            assert fake_live.started is True
            assert fake_live.stopped is False

        # Disconnected ws_live: live is stopped, but session/pipeline remain alive!
        assert fake_live.stopped is True
        assert session.stopped is False
        assert pipeline.stopped is False

    # Disconnected /ws: now session & pipeline are stopped
    assert session.stopped is True
    assert pipeline.stopped is True


def test_ws_live_broadcasts_to_multiple_live_clients() -> None:
    fake_live = FakeLiveView()
    session = FakeSession("judy")
    pipeline = FakePipeline(session)

    app = create_app(
        session_factory=lambda u: session,
        pipeline_factory=lambda s: pipeline,
        live_view_factory=lambda b: fake_live,
    )
    test_client = TestClient(app)

    with test_client.websocket_connect("/ws?user_id=judy"):
        with test_client.websocket_connect("/ws/live?user_id=judy") as ws1:
            with test_client.websocket_connect("/ws/live?user_id=judy") as ws2:
                # ws1 takes control
                ws1.send_json({"type": "control", "take": True})
                msg1 = ws1.receive_json()
                msg2 = ws2.receive_json()

                assert msg1["type"] == "control"
                assert msg1["user_in_control"] is True
                assert msg2["type"] == "control"
                assert msg2["user_in_control"] is True


def test_ws_live_invalid_messages() -> None:
    fake_live = FakeLiveView()
    session = FakeSession("kyle")
    pipeline = FakePipeline(session)

    app = create_app(
        session_factory=lambda u: session,
        pipeline_factory=lambda s: pipeline,
        live_view_factory=lambda b: fake_live,
    )
    test_client = TestClient(app)

    with test_client.websocket_connect("/ws?user_id=kyle"):
        with test_client.websocket_connect("/ws/live?user_id=kyle") as ws_live:
            # 1. Non-JSON string
            ws_live.send_text("NOT JSON!")
            err1 = ws_live.receive_json()
            assert err1["type"] == "error"
            assert "Invalid JSON" in err1["message"]

            # 2. Missing message type
            ws_live.send_json({"no_type": 123})
            err2 = ws_live.receive_json()
            assert err2["type"] == "error"
            assert "Missing message type" in err2["message"]

            # 3. Unknown message type when in control
            ws_live.send_json({"type": "control", "take": True})
            assert ws_live.receive_json()["user_in_control"] is True

            ws_live.send_json({"type": "unsupported_command"})
            err3 = ws_live.receive_json()
            assert err3["type"] == "error"
            assert "Unknown message type" in err3["message"]

