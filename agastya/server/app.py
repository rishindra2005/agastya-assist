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


def create_app(
    session_factory: Callable[[str], AgentSession] | None = None,
    pipeline_factory: Callable[[AgentSession], VoicePipeline] | None = None,
) -> FastAPI:
    """Create and configure the Agastya Assist FastAPI application."""
    sess_factory = session_factory if session_factory is not None else default_session_factory
    pipe_factory = pipeline_factory if pipeline_factory is not None else default_pipeline_factory

    app = FastAPI(title="Agastya Assist API")

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

    return app


app = create_app()
