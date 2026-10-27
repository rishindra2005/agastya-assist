"""FastAPI application and WebSocket handler for Agastya Assist."""

from __future__ import annotations

import asyncio
import base64
import datetime
import json
import logging
from pathlib import Path
import time
from typing import Any, Callable
import uuid

from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agastya.agent.session import AgentSession
from agastya.portals.loader import load_playbooks
from agastya.profile.fields import FIELDS, masked
from agastya.profile.store import ProfileStore
from agastya.voice.pipeline import VoicePipeline
from agastya.voice.types import SUPPORTED_LANGUAGES

logger = logging.getLogger(__name__)


class ProfileUpdate(BaseModel):
    value: str


async def _maybe_await(obj: Any) -> Any:
    """Await obj if it is an awaitable or future, otherwise return it."""
    if asyncio.iscoroutine(obj) or asyncio.isfuture(obj):
        return await obj
    return obj


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEMO_PORTALS_DIR = REPO_ROOT / "demo_portals"
MAX_DOCUMENT_SIZE = 10 * 1024 * 1024  # 10 MB
ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}


class FallbackDocumentStore:
    _store: dict[str, list[dict[str, Any]]] = {}

    def __init__(self, user_id: str = "default", data_dir: Path | None = None) -> None:
        self.user_id = user_id
        if user_id not in FallbackDocumentStore._store:
            FallbackDocumentStore._store[user_id] = []

    def add(self, filename: str, data: bytes) -> dict[str, Any]:
        doc_id = f"doc_{uuid.uuid4().hex[:8]}"
        lower_name = filename.lower()
        if "form16" in lower_name or "form 16" in lower_name or "form-16" in lower_name:
            kind = "form16"
            extracted = {
                "kind": "form16",
                "assessment_year": "2026-27",
                "employer_name": "Demo Infotech",
                "gross_salary": 1275000,
                "tds_total": 45000,
            }
        elif lower_name.endswith(".pdf"):
            kind = "pdf"
            extracted = {}
        elif lower_name.endswith((".png", ".jpg", ".jpeg")):
            kind = "image"
            extracted = {}
        else:
            kind = "document"
            extracted = {}

        doc = {
            "id": doc_id,
            "user_id": self.user_id,
            "filename": filename,
            "kind": kind,
            "path": f"/tmp/{doc_id}_{filename}",
            "uploaded_at": datetime.datetime.now().isoformat(),
            "extracted": extracted,
        }
        FallbackDocumentStore._store[self.user_id].append(doc)
        return doc

    def list(self) -> list[dict[str, Any]]:
        return list(FallbackDocumentStore._store.get(self.user_id, []))

    def get(self, doc_id: str) -> dict[str, Any] | None:
        for doc in FallbackDocumentStore._store.get(self.user_id, []):
            if doc.get("id") == doc_id:
                return doc
        return None


def get_document_store(user_id: str = "default") -> Any:
    for mod_name in ("agastya.documents", "agastya.documents.store"):
        try:
            mod = __import__(mod_name, fromlist=["DocumentStore"])
            cls = getattr(mod, "DocumentStore", None)
            if cls is not None:
                return cls(user_id=user_id)
        except ImportError:
            pass
    return FallbackDocumentStore(user_id=user_id)


def format_document_summary(doc: Any) -> str:
    if isinstance(doc, dict):
        if doc.get("summary"):
            return str(doc["summary"])
        extracted = doc.get("extracted") or {}
        kind = doc.get("kind", "document")
        filename = doc.get("filename", "document")
    else:
        if getattr(doc, "summary", None):
            return str(doc.summary)
        extracted = getattr(doc, "extracted", {}) or {}
        kind = getattr(doc, "kind", "document")
        filename = getattr(doc, "filename", "document")

    if (
        kind == "form16"
        or extracted.get("kind") == "form16"
        or "gross_salary" in extracted
        or "assessment_year" in extracted
    ):
        parts = ["Form 16"]
        if ay := extracted.get("assessment_year"):
            parts.append(f"AY {ay}")
        if emp := extracted.get("employer_name"):
            parts.append(str(emp))
        if gross := extracted.get("gross_salary"):
            try:
                parts.append(f"gross ₹{int(gross):,}")
            except Exception:
                parts.append(f"gross ₹{gross}")
        if tds := extracted.get("tds_total"):
            try:
                parts.append(f"TDS ₹{int(tds):,}")
            except Exception:
                parts.append(f"TDS ₹{tds}")
        if len(parts) > 1:
            return " · ".join(parts)
        return "Form 16"

    if kind and kind not in ("document", "unknown"):
        return f"{filename} ({kind})"
    return filename


def default_session_factory(user_id: str) -> AgentSession:
    return AgentSession(user_id=user_id)


def default_pipeline_factory(session: AgentSession) -> VoicePipeline:
    return VoicePipeline(session)


def default_live_view_factory(browser: Any) -> Any:
    try:
        from agastya.browser.live import LiveView
        return LiveView(browser)
    except ImportError as e:
        raise RuntimeError(f"LiveView is not available: {e}") from e


def create_app(
    session_factory: Callable[[str], AgentSession] | None = None,
    pipeline_factory: Callable[[AgentSession], VoicePipeline] | None = None,
    live_view_factory: Callable[[Any], Any] | None = None,
    demo_portals_dir: Path | str | None = None,
) -> FastAPI:
    """Create and configure the Agastya Assist FastAPI application."""
    sess_factory = session_factory if session_factory is not None else default_session_factory
    pipe_factory = pipeline_factory if pipeline_factory is not None else default_pipeline_factory
    live_factory = live_view_factory if live_view_factory is not None else default_live_view_factory

    app = FastAPI(title="Agastya Assist API")
    app.state.sessions = {}
    app.state.live_sockets = {}

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    static_dir = Path(__file__).parent / "static"
    app.mount(
        "/static",
        StaticFiles(directory=str(static_dir), check_dir=False),
        name="static",
    )

    demo_dir = Path(demo_portals_dir if demo_portals_dir is not None else DEMO_PORTALS_DIR)
    demo_dir.mkdir(parents=True, exist_ok=True)
    app.mount(
        "/mock",
        StaticFiles(directory=str(demo_dir), html=True, check_dir=False),
        name="mock",
    )

    async def broadcast_control(user_id: str, is_in_control: bool) -> None:
        sockets = dict(app.state.live_sockets.get(user_id, {}))
        payload = {"type": "control", "user_in_control": is_in_control}
        for ws, lock in sockets.items():
            try:
                async with lock:
                    await ws.send_json(payload)
            except Exception:
                pass

    @app.get("/")
    async def get_index():
        index_file = static_dir / "index.html"
        if not index_file.is_file():
            raise HTTPException(status_code=404, detail="index.html not found")
        return FileResponse(str(index_file))

    @app.get("/manifest.webmanifest")
    async def get_manifest():
        manifest_file = static_dir / "manifest.webmanifest"
        if not manifest_file.is_file():
            raise HTTPException(status_code=404, detail="manifest.webmanifest not found")
        return FileResponse(str(manifest_file), media_type="application/manifest+json")

    @app.get("/manifest.json")
    async def get_manifest_json():
        return await get_manifest()

    @app.get("/sw.js")
    async def get_service_worker():
        sw_file = static_dir / "sw.js"
        if not sw_file.is_file():
            raise HTTPException(status_code=404, detail="sw.js not found")
        return FileResponse(str(sw_file), media_type="application/javascript")

    @app.get("/api/health")
    async def health():
        return {"ok": True}

    @app.get("/api/languages")
    async def get_languages():
        return SUPPORTED_LANGUAGES

    @app.get("/api/playbooks")
    async def get_playbooks():
        try:
            playbooks = load_playbooks()
            return [
                {
                    "id": pb.id,
                    "portal": pb.portal,
                    "title_en": pb.title_en,
                    "title_hi": pb.title_hi,
                    "live_url": pb.live_url,
                    "requires_login": pb.requires_login,
                }
                for pb in playbooks.values()
            ]
        except Exception as e:
            logger.exception("Error loading playbooks: %s", e)
            return []

    @app.get("/api/profile")
    async def get_profile(user_id: str = "default"):
        with ProfileStore(user_id=user_id) as store:
            records = store.all_records()
            result: dict[str, dict[str, Any]] = {}
            for name, info in FIELDS.items():
                if name in records:
                    rec = records[name]
                    raw_val = rec.get("value")
                    val = (
                        masked(name, raw_val)
                        if (info.sensitive and raw_val is not None)
                        else raw_val
                    )
                    result[name] = {
                        "value": val,
                        "label_en": info.label_en,
                        "label_hi": info.label_hi,
                        "source": rec.get("source"),
                        "updated_at": rec.get("updated_at"),
                    }
                else:
                    result[name] = {
                        "value": None,
                        "label_en": info.label_en,
                        "label_hi": info.label_hi,
                        "source": None,
                        "updated_at": None,
                    }
            return result

    @app.put("/api/profile/{field}")
    async def put_profile(field: str, body: ProfileUpdate, user_id: str = "default"):
        if field not in FIELDS:
            raise HTTPException(status_code=422, detail=f"Unknown profile field: '{field}'")
        try:
            with ProfileStore(user_id=user_id) as store:
                store.set(field, body.value)
                rec = store.get_record(field)
                masked_val = store.masked(field)
                return {
                    "ok": True,
                    "field": field,
                    "value": masked_val,
                    "source": rec["source"] if rec else "user",
                    "updated_at": rec["updated_at"] if rec else None,
                }
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    @app.delete("/api/profile/{field}")
    async def delete_profile(field: str, user_id: str = "default"):
        with ProfileStore(user_id=user_id) as store:
            deleted = store.delete(field)
            return {"ok": True, "field": field, "deleted": deleted}

    @app.post("/api/documents")
    async def upload_document(
        file: UploadFile = File(...),
        user_id: str = "default",
    ):
        filename = file.filename or "document"
        ext = Path(filename).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=415,
                detail="Unsupported Media Type: only .pdf, .png, .jpg files are allowed",
            )

        content = await file.read()
        if len(content) > MAX_DOCUMENT_SIZE:
            raise HTTPException(
                status_code=413,
                detail="Payload Too Large: file exceeds 10 MB limit",
            )

        store = get_document_store(user_id)
        doc = store.add(filename, content)

        doc_id = getattr(doc, "id", None) if not isinstance(doc, dict) else doc.get("id")
        doc_kind = (
            getattr(doc, "kind", "document")
            if not isinstance(doc, dict)
            else doc.get("kind", "document")
        )
        doc_filename = (
            getattr(doc, "filename", filename)
            if not isinstance(doc, dict)
            else doc.get("filename", filename)
        )
        summary = format_document_summary(doc)

        sess_tuple = app.state.sessions.get(user_id)
        if sess_tuple is not None:
            session, _ = sess_tuple
            if hasattr(session, "bus") and hasattr(session.bus, "emit"):
                session.bus.emit(
                    "document_uploaded",
                    id=doc_id,
                    filename=doc_filename,
                    kind=doc_kind,
                    summary=summary,
                )
            if hasattr(session, "send_user_message") and callable(session.send_user_message):
                user_msg = f"[user uploaded document {doc_filename} id={doc_id} kind={doc_kind}]"
                try:
                    await _maybe_await(session.send_user_message(user_msg))
                except Exception as err:
                    logger.warning("Failed to send user message for uploaded document: %s", err)

        return {
            "id": doc_id,
            "filename": doc_filename,
            "kind": doc_kind,
            "summary": summary,
        }

    @app.get("/api/documents")
    async def get_documents(user_id: str = "default"):
        store = get_document_store(user_id)
        docs = store.list()
        result = []
        for d in docs:
            doc_id = getattr(d, "id", None) if not isinstance(d, dict) else d.get("id")
            filename = (
                getattr(d, "filename", "")
                if not isinstance(d, dict)
                else d.get("filename", "")
            )
            kind = (
                getattr(d, "kind", "document")
                if not isinstance(d, dict)
                else d.get("kind", "document")
            )
            uploaded_at = (
                getattr(d, "uploaded_at", "")
                if not isinstance(d, dict)
                else d.get("uploaded_at", "")
            )
            summary = format_document_summary(d)
            result.append({
                "id": doc_id,
                "filename": filename,
                "kind": kind,
                "summary": summary,
                "uploaded_at": uploaded_at,
            })
        return result

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket, user_id: str = "default"):
        await websocket.accept()

        old_entry = app.state.sessions.get(user_id)
        if old_entry is not None:
            old_session, old_pipeline = old_entry
            # Live sockets are bound to the old session's browser: close them so clients
            # reconnect to the new session (otherwise their input goes to a dead browser).
            for live_ws in list(app.state.live_sockets.get(user_id, {})):
                try:
                    await live_ws.close(code=4409)
                except Exception:
                    pass
            if hasattr(old_pipeline, "stop") and callable(old_pipeline.stop):
                try:
                    await _maybe_await(old_pipeline.stop())
                except Exception:
                    logger.exception("Error stopping replaced pipeline")
            if hasattr(old_session, "stop") and callable(old_session.stop):
                try:
                    await _maybe_await(old_session.stop())
                except Exception:
                    logger.exception("Error stopping replaced session")

        try:
            session = sess_factory(user_id)
            pipeline = pipe_factory(session)
        except Exception as e:
            logger.exception("Error instantiating session or pipeline: %s", e)
            await websocket.send_json({
                "type": "error",
                "data": {"error": str(e)},
                "ts": time.time(),
            })
            await websocket.close(code=1011)
            return

        app.state.sessions[user_id] = (session, pipeline)

        sub_queue: asyncio.Queue[Any] | None = None
        if hasattr(session, "bus") and hasattr(session.bus, "subscribe"):
            sub_queue = session.bus.subscribe()

        try:
            if hasattr(session, "start") and callable(session.start):
                await _maybe_await(session.start())
            if hasattr(pipeline, "start") and callable(pipeline.start):
                await _maybe_await(pipeline.start())
        except Exception as e:
            logger.exception("Failed to start session or pipeline: %s", e)
            current = app.state.sessions.get(user_id)
            if current is not None and current[0] is session and current[1] is pipeline:
                app.state.sessions.pop(user_id, None)

            if sub_queue is not None and hasattr(session, "bus") and hasattr(session.bus, "unsubscribe"):
                session.bus.unsubscribe(sub_queue)
            await websocket.send_json({
                "type": "error",
                "data": {"error": str(e)},
                "ts": time.time(),
            })
            await websocket.close(code=1011)
            if hasattr(pipeline, "stop") and callable(pipeline.stop):
                try:
                    await _maybe_await(pipeline.stop())
                except Exception:
                    pass
            if hasattr(session, "stop") and callable(session.stop):
                try:
                    await _maybe_await(session.stop())
                except Exception:
                    pass
            return

        bg_tasks: set[asyncio.Task] = set()

        async def forward_events():
            if sub_queue is None:
                try:
                    while True:
                        await asyncio.sleep(3600)
                except asyncio.CancelledError:
                    pass
                return

            try:
                while True:
                    event = await sub_queue.get()
                    if hasattr(event, "to_json") and callable(event.to_json):
                        payload = event.to_json()
                    elif isinstance(event, dict):
                        payload = event
                    else:
                        payload = {
                            "type": getattr(event, "type", "status"),
                            "data": getattr(event, "data", {}),
                            "ts": getattr(event, "ts", time.time()),
                        }
                    await websocket.send_json(payload)
            except (asyncio.CancelledError, WebSocketDisconnect):
                pass
            except Exception as e:
                logger.exception("Error forwarding event: %s", e)
                raise

        async def read_client_messages():
            try:
                while True:
                    raw_text = await websocket.receive_text()
                    try:
                        msg = json.loads(raw_text)
                        if not isinstance(msg, dict):
                            raise ValueError("Message must be a JSON object")
                    except Exception as err:
                        await websocket.send_json({
                            "type": "error",
                            "data": {"error": f"Invalid JSON: {err}"},
                            "ts": time.time(),
                        })
                        continue

                    msg_type = msg.get("type")
                    if not msg_type:
                        await websocket.send_json({
                            "type": "error",
                            "data": {"error": "Missing message type"},
                            "ts": time.time(),
                        })
                        continue

                    try:
                        if msg_type == "text":
                            text = msg.get("text", "")
                            lang = msg.get("language_code")
                            if hasattr(session, "send_user_message") and callable(session.send_user_message):
                                try:
                                    await _maybe_await(session.send_user_message(text, language_code=lang))
                                except TypeError:
                                    await _maybe_await(session.send_user_message(text))

                        elif msg_type == "audio":
                            audio_b64 = msg.get("audio_b64", "")
                            if not audio_b64:
                                await websocket.send_json({
                                    "type": "error",
                                    "data": {"error": "Missing 'audio_b64'"},
                                    "ts": time.time(),
                                })
                                continue
                            try:
                                audio_bytes = base64.b64decode(audio_b64)
                            except Exception as b64_err:
                                await websocket.send_json({
                                    "type": "error",
                                    "data": {"error": f"Invalid base64 audio: {b64_err}"},
                                    "ts": time.time(),
                                })
                                continue

                            if hasattr(pipeline, "handle_audio") and callable(pipeline.handle_audio):
                                async def _run_audio(b: bytes):
                                    try:
                                        await _maybe_await(pipeline.handle_audio(b, "audio.wav"))
                                    except Exception as a_err:
                                        logger.exception("Error handling audio: %s", a_err)
                                        if hasattr(session, "bus") and hasattr(session.bus, "emit"):
                                            session.bus.emit("error", error=f"Audio error: {a_err}")

                                task = asyncio.create_task(_run_audio(audio_bytes))
                                bg_tasks.add(task)
                                task.add_done_callback(bg_tasks.discard)

                        elif msg_type == "answer":
                            qid = msg.get("question_id", "")
                            ans = msg.get("answer", "")
                            if hasattr(session, "answer_question") and callable(session.answer_question):
                                await _maybe_await(session.answer_question(qid, ans))

                        elif msg_type == "set_language":
                            lang = msg.get("language_code")
                            if lang:
                                if hasattr(session, "language_code"):
                                    session.language_code = lang
                                if hasattr(pipeline, "language_code"):
                                    pipeline.language_code = lang
                                if hasattr(pipeline, "set_language") and callable(pipeline.set_language):
                                    await _maybe_await(pipeline.set_language(lang))

                        elif msg_type == "tts":
                            enabled = msg.get("enabled", True)
                            if hasattr(pipeline, "auto_speak"):
                                pipeline.auto_speak = bool(enabled)
                            elif hasattr(pipeline, "set_auto_speak") and callable(pipeline.set_auto_speak):
                                await _maybe_await(pipeline.set_auto_speak(bool(enabled)))

                        elif msg_type == "control":
                            take = msg.get("take", True)
                            if isinstance(take, str):
                                take = take.lower() in ("true", "1", "take")
                            else:
                                take = bool(take)
                            if take:
                                if hasattr(session, "take_control") and callable(session.take_control):
                                    await _maybe_await(session.take_control())
                                else:
                                    session.user_in_control = True
                                    if hasattr(session, "bus") and hasattr(session.bus, "emit"):
                                        session.bus.emit("status", status="user_control")
                            else:
                                note = msg.get("note", "")
                                if hasattr(session, "release_control") and callable(session.release_control):
                                    try:
                                        await _maybe_await(session.release_control(note))
                                    except TypeError:
                                        await _maybe_await(session.release_control())
                                else:
                                    session.user_in_control = False
                                    if hasattr(session, "bus") and hasattr(session.bus, "emit"):
                                        session.bus.emit("status", status="agent_control")

                            current_control = getattr(session, "user_in_control", take)
                            await broadcast_control(user_id, current_control)

                        else:
                            await websocket.send_json({
                                "type": "error",
                                "data": {"error": f"Unknown message type '{msg_type}'"},
                                "ts": time.time(),
                            })
                    except Exception as dispatch_err:
                        logger.exception("Error dispatching message: %s", dispatch_err)
                        await websocket.send_json({
                            "type": "error",
                            "data": {"error": str(dispatch_err)},
                            "ts": time.time(),
                        })
            except (asyncio.CancelledError, WebSocketDisconnect):
                pass

        forward_task = asyncio.create_task(forward_events())
        dispatch_task = asyncio.create_task(read_client_messages())

        try:
            done, pending = await asyncio.wait(
                [forward_task, dispatch_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        finally:
            current = app.state.sessions.get(user_id)
            if current is not None and current[0] is session and current[1] is pipeline:
                app.state.sessions.pop(user_id, None)

            for t in list(bg_tasks):
                t.cancel()

            if sub_queue is not None and hasattr(session, "bus") and hasattr(session.bus, "unsubscribe"):
                session.bus.unsubscribe(sub_queue)

            if hasattr(pipeline, "stop") and callable(pipeline.stop):
                try:
                    await _maybe_await(pipeline.stop())
                except Exception:
                    logger.exception("Error stopping pipeline")

            if hasattr(session, "stop") and callable(session.stop):
                try:
                    await _maybe_await(session.stop())
                except Exception:
                    logger.exception("Error stopping session")

    @app.websocket("/ws/live")
    async def ws_live_endpoint(websocket: WebSocket, user_id: str = "default"):
        sess_tuple = app.state.sessions.get(user_id)
        if sess_tuple is None:
            await websocket.close(code=4404)
            return

        session, pipeline = sess_tuple
        await websocket.accept()

        browser = getattr(session, "browser", None)
        try:
            live = live_factory(browser)
        except Exception as e:
            logger.exception("Error creating LiveView: %s", e)
            await websocket.close(code=1011)
            return

        send_lock = asyncio.Lock()
        if not hasattr(app.state, "live_sockets"):
            app.state.live_sockets = {}
        app.state.live_sockets.setdefault(user_id, {})[websocket] = send_lock

        latest_frame: bytes | None = None
        pending_meta: dict[str, Any] | None = None
        last_meta: dict[str, Any] | None = None
        frame_event = asyncio.Event()

        async def on_frame(jpeg_bytes: bytes, meta: dict | None = None) -> None:
            nonlocal latest_frame, pending_meta, last_meta
            if meta is not None and meta != last_meta:
                pending_meta = dict(meta)
                last_meta = dict(meta)
            latest_frame = jpeg_bytes
            frame_event.set()

        async def frame_sender_loop() -> None:
            nonlocal latest_frame, pending_meta
            last_sent: bytes | None = None
            try:
                while True:
                    # Chrome only sends screencast frames when the page repaints; resend the last
                    # frame every 2s on a static page so the client knows the stream is alive.
                    try:
                        await asyncio.wait_for(frame_event.wait(), timeout=2.0)
                    except asyncio.TimeoutError:
                        if last_sent is not None:
                            async with send_lock:
                                await websocket.send_bytes(last_sent)
                        continue
                    frame_event.clear()
                    meta_to_send = pending_meta
                    pending_meta = None
                    frame_to_send = latest_frame
                    latest_frame = None
                    if frame_to_send is not None:
                        last_sent = frame_to_send

                    if meta_to_send is not None:
                        async with send_lock:
                            await websocket.send_json({"type": "meta", **meta_to_send})
                    if frame_to_send is not None:
                        async with send_lock:
                            await websocket.send_bytes(frame_to_send)
            except (asyncio.CancelledError, WebSocketDisconnect):
                pass
            except Exception as e:
                logger.exception("Error in frame_sender_loop: %s", e)

        async def read_client_messages() -> None:
            try:
                while True:
                    raw_text = await websocket.receive_text()
                    try:
                        msg = json.loads(raw_text)
                        if not isinstance(msg, dict):
                            raise ValueError("Message must be a JSON object")
                    except Exception as err:
                        async with send_lock:
                            await websocket.send_json({
                                "type": "error",
                                "message": f"Invalid JSON: {err}",
                                "data": {"error": f"Invalid JSON: {err}"},
                                "ts": time.time(),
                            })
                        continue

                    msg_type = msg.get("type")
                    if not msg_type:
                        async with send_lock:
                            await websocket.send_json({
                                "type": "error",
                                "message": "Missing message type",
                                "data": {"error": "Missing message type"},
                                "ts": time.time(),
                            })
                        continue

                    if msg_type == "control":
                        take = msg.get("take", True)
                        if isinstance(take, str):
                            take = take.lower() in ("true", "1", "take")
                        else:
                            take = bool(take)

                        try:
                            if take:
                                if hasattr(session, "take_control") and callable(session.take_control):
                                    await _maybe_await(session.take_control())
                                else:
                                    session.user_in_control = True
                                    if hasattr(session, "bus") and hasattr(session.bus, "emit"):
                                        session.bus.emit("status", status="user_control")
                            else:
                                note = msg.get("note", "")
                                if hasattr(session, "release_control") and callable(session.release_control):
                                    try:
                                        await _maybe_await(session.release_control(note))
                                    except TypeError:
                                        await _maybe_await(session.release_control())
                                else:
                                    session.user_in_control = False
                                    if hasattr(session, "bus") and hasattr(session.bus, "emit"):
                                        session.bus.emit("status", status="agent_control")

                            current_control = getattr(session, "user_in_control", take)
                            await broadcast_control(user_id, current_control)
                        except Exception as ctrl_err:
                            logger.exception("Error handling control message: %s", ctrl_err)
                            async with send_lock:
                                await websocket.send_json({
                                    "type": "error",
                                    "message": str(ctrl_err),
                                    "data": {"error": str(ctrl_err)},
                                    "ts": time.time(),
                                })
                        continue

                    # Input when not user_in_control -> {"type":"error","message":"take control first"}
                    is_in_control = getattr(session, "user_in_control", False)
                    if not is_in_control:
                        async with send_lock:
                            await websocket.send_json({
                                "type": "error",
                                "message": "take control first",
                                "data": {"error": "take control first"},
                                "ts": time.time(),
                            })
                        continue

                    try:
                        if msg_type == "mouse":
                            kind = msg.get("kind", "click")
                            x = float(msg.get("x", 0.0))
                            y = float(msg.get("y", 0.0))
                            button = msg.get("button", "left")
                            delta_y = float(msg.get("delta_y", 0.0))
                            click_count = int(msg.get("click_count", 1))
                            if hasattr(live, "mouse") and callable(live.mouse):
                                await _maybe_await(
                                    live.mouse(kind, x, y, button=button, delta_y=delta_y, click_count=click_count)
                                )

                        elif msg_type == "key":
                            kind = msg.get("kind", "press")
                            key_val = msg.get("key", "")
                            code = msg.get("code", "")
                            text_val = msg.get("text", "")
                            if hasattr(live, "key") and callable(live.key):
                                await _maybe_await(
                                    live.key(kind, key_val, code=code, text=text_val)
                                )

                        elif msg_type == "text":
                            text_val = msg.get("text", "")
                            if hasattr(live, "insert_text") and callable(live.insert_text):
                                await _maybe_await(live.insert_text(text_val))
                            elif hasattr(live, "text") and callable(live.text):
                                await _maybe_await(live.text(text_val))

                        elif msg_type == "nav":
                            action = msg.get("action", "")
                            if hasattr(live, "nav") and callable(live.nav):
                                await _maybe_await(live.nav(action))

                        else:
                            async with send_lock:
                                await websocket.send_json({
                                    "type": "error",
                                    "message": f"Unknown message type '{msg_type}'",
                                    "data": {"error": f"Unknown message type '{msg_type}'"},
                                    "ts": time.time(),
                                })
                    except Exception as action_err:
                        logger.exception("Error executing %s action: %s", msg_type, action_err)
                        async with send_lock:
                            await websocket.send_json({
                                "type": "error",
                                "message": str(action_err),
                                "data": {"error": str(action_err)},
                                "ts": time.time(),
                            })
            except (asyncio.CancelledError, WebSocketDisconnect):
                pass

        sender_task = asyncio.create_task(frame_sender_loop())
        reader_task = asyncio.create_task(read_client_messages())

        try:
            if hasattr(live, "start") and callable(live.start):
                await _maybe_await(live.start(on_frame))

            done, pending = await asyncio.wait(
                [sender_task, reader_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        finally:
            sender_task.cancel()
            reader_task.cancel()

            if hasattr(app.state, "live_sockets") and user_id in app.state.live_sockets:
                app.state.live_sockets[user_id].pop(websocket, None)
                if not app.state.live_sockets[user_id]:
                    app.state.live_sockets.pop(user_id, None)

            # On disconnect: live.stop() only
            if hasattr(live, "stop") and callable(live.stop):
                try:
                    await _maybe_await(live.stop())
                except Exception:
                    logger.exception("Error stopping LiveView")

    return app


app = create_app()
