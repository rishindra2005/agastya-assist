"""FastAPI application and WebSocket handler for Agastya Assist."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from pathlib import Path
import time
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
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
