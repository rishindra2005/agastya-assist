"""Command-line interface for Agastya Assist.

Provides:
  - agastya chat: interactive terminal chat and voice interaction with the agent
  - agastya profile: inspect and manage user profile entries
  - agastya serve: launch the FastAPI web server
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import logging
import sys
from typing import Any

from agastya.config import get_settings
from agastya.profile.fields import FIELDS
from agastya.profile.store import ProfileStore
from agastya.voice.audio_io import play_wav, record_until_enter

logger = logging.getLogger(__name__)

# ANSI color codes
CYAN = "\033[36m"
YELLOW = "\033[33m"
DIM = "\033[2m"
RED = "\033[31m"
RESET = "\033[0m"


def format_event(event: Any, verbose: bool = False) -> str | None:
    """Format an AgentEvent or event dictionary for terminal display.

    Returns None for events that should not produce terminal output in the given mode.
    """
    if isinstance(event, dict):
        event_type = event.get("type")
        data = event.get("data", {})
    else:
        event_type = getattr(event, "type", None)
        data = getattr(event, "data", {})

    if not isinstance(data, dict):
        data = {}

    if event_type == "assistant_text":
        text = data.get("text", "")
        return f"{CYAN}Agastya: {text}{RESET}"

    if event_type == "ask_user":
        question = data.get("question", "")
        return f"❓ {question}"

    if event_type == "blocked":
        reason = data.get("reason") or "Action blocked"
        return f"{YELLOW}⚠ Your turn: {reason}{RESET}"

    if event_type == "handoff":
        reason = data.get("reason") or "Control handed back to user"
        return f"{YELLOW}⚠ Your turn: {reason}{RESET}"

    if event_type == "status":
        if data.get("handoff"):
            reason = data.get("reason") or "Control handed back to user"
            return f"{YELLOW}⚠ Your turn: {reason}{RESET}"
        if verbose and data.get("status"):
            return f"{DIM}  · status: {data.get('status')}{RESET}"
        return None

    if event_type == "screenshot":
        url = data.get("url", "")
        return f"{DIM}  · page: {url}{RESET}"

    if event_type == "tool_call":
        if not verbose:
            return None
        name = data.get("name", "tool")
        tool_name = name.removeprefix("mcp__agastya__")
        inp = (
            data.get("input")
            if data.get("input") is not None
            else data.get("args")
            if data.get("args") is not None
            else data.get("arguments")
        )
        if isinstance(inp, str) and inp.strip().startswith("{") and inp.strip().endswith("}"):
            try:
                import json
                inp = json.loads(inp)
            except Exception:
                pass

        if isinstance(inp, dict):
            args_str = ", ".join(f"{k}={v!r}" for k, v in inp.items())
        elif inp is not None:
            args_str = str(inp)
        else:
            args_str = ""
        return f"{DIM}  · {tool_name}({args_str}){RESET}"

    if event_type == "error":
        if verbose:
            err = data.get("error") or data.get("message") or "Unknown error"
            return f"{RED}Error: {err}{RESET}"
        return None

    if event_type == "transcript":
        if verbose:
            text = data.get("text", "")
            return f"{DIM}  · transcript: {text}{RESET}"
        return None

    if event_type == "profile_updated":
        if verbose:
            fld = data.get("field", "")
            return f"{DIM}  · profile updated: {fld}{RESET}"
        return None

    if event_type == "tool_result":
        if verbose:
            res = data.get("result", "")
            return f"{DIM}  · tool_result: {res}{RESET}"
        return None

    if event_type == "done":
        if verbose:
            return f"{DIM}  · done{RESET}"
        return None

    if event_type == "tts":
        return None

    return None


def cmd_profile_show(args: argparse.Namespace) -> int:
    """Print a table of all FIELDS with masked values."""
    with ProfileStore(user_id=args.user) as store:
        col_field = 24
        col_val = 20
        col_label = 32
        header = f"{'Field':<{col_field}} {'Value':<{col_val}} {'Label':<{col_label}}"
        print(header)
        print("-" * len(header))
        for field, info in FIELDS.items():
            val = store.masked(field) or "-"
            label = info.get("label_en", "")
            print(f"{field:<{col_field}} {val:<{col_val}} {label:<{col_label}}")
    return 0


def cmd_profile_set(args: argparse.Namespace) -> int:
    """Set and validate a profile field value."""
    if args.field not in FIELDS:
        sys.stderr.write(f"Validation error: Unknown field '{args.field}'.\n")
        return 2

    with ProfileStore(user_id=args.user) as store:
        try:
            store.set(args.field, args.value)
        except ValueError as exc:
            sys.stderr.write(f"Validation error: {exc}\n")
            return 2

        masked_val = store.masked(args.field)
        print(f"Set {args.field} = {masked_val}")
    return 0


def cmd_profile_delete(args: argparse.Namespace) -> int:
    """Delete a profile field."""
    with ProfileStore(user_id=args.user) as store:
        deleted = store.delete(args.field)
        if deleted:
            print(f"Deleted {args.field}")
        else:
            print(f"Field '{args.field}' was not set")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    """Run the FastAPI web application with uvicorn."""
    import uvicorn

    uvicorn.run(
        "agastya.server.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


async def run_chat(args: argparse.Namespace) -> int:
    """Run interactive terminal chat / voice loop."""
    from agastya.agent.session import AgentSession
    from agastya.browser.driver import BrowserDriver
    from agastya.voice.pipeline import VoicePipeline

    browser = BrowserDriver(headless=not args.headed)
    session = AgentSession(user_id=args.user, browser=browser)
    session.language_code = args.lang
    pipeline = VoicePipeline(session, auto_speak=args.voice)

    events_q = session.bus.subscribe()
    loop = asyncio.get_running_loop()

    async def _event_listener() -> None:
        while True:
            try:
                event = await events_q.get()
            except asyncio.CancelledError:
                break
            try:
                msg = format_event(event, verbose=args.verbose)
                if msg is not None:
                    print(msg, flush=True)

                event_type = event.get("type") if isinstance(event, dict) else getattr(event, "type", None)
                data = event.get("data", {}) if isinstance(event, dict) else getattr(event, "data", {})

                if args.voice and event_type == "tts":
                    audio_b64 = data.get("audio_b64")
                    if audio_b64:
                        wav_bytes = base64.b64decode(audio_b64)
                        try:
                            await play_wav(wav_bytes)
                        except Exception as exc:
                            logger.warning("Audio playback failed: %s", exc)
            except Exception as exc:
                logger.error("Error in event listener: %s", exc)
            finally:
                events_q.task_done()

    listener_task = asyncio.create_task(_event_listener())

    try:
        await session.start()
        await pipeline.start()

        if args.voice:
            print("Agastya voice chat started. Press Enter to record, type text, or 'exit' to quit.")
        else:
            print("Agastya chat started. Type your message, or 'exit' (or Ctrl-D) to quit.")

        while True:
            try:
                if args.voice:
                    user_line = await loop.run_in_executor(
                        None, input, "\n[Press Enter to record voice, or type message/exit]: "
                    )
                else:
                    user_line = await loop.run_in_executor(None, input, "> ")
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if user_line is None:
                break

            stripped = user_line.strip()
            if stripped.lower() == "exit":
                break

            if args.voice and not stripped:
                print("Recording... Press Enter to stop.")
                try:
                    audio_data = await record_until_enter()
                    if audio_data:
                        print("Transcribing...")
                        await pipeline.handle_audio(audio_data)
                except Exception as exc:
                    print(f"Audio error: {exc}")
            elif stripped:
                await pipeline.handle_text(stripped, language_code=args.lang)

    finally:
        session.bus.unsubscribe(events_q)
        listener_task.cancel()
        try:
            await listener_task
        except (asyncio.CancelledError, Exception):
            pass

        try:
            await pipeline.stop()
        except Exception:
            pass

        try:
            await session.stop()
        except Exception:
            pass

    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build and return the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="agastya",
        description="Agastya Assist — Voice-first AI assistant for Indian government portals",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # chat subcommand
    chat_p = subparsers.add_parser("chat", help="Start interactive agent chat session")
    chat_p.add_argument("--user", default="default", help="User profile ID (default: 'default')")
    chat_p.add_argument(
        "--lang",
        default=get_settings().DEFAULT_LANGUAGE,
        help="Initial language code (default from settings)",
    )
    chat_p.add_argument(
        "--voice", action="store_true", default=False, help="Enable push-to-talk voice mode"
    )
    chat_p.add_argument(
        "--headed", action="store_true", default=False, help="Run browser in visible (headed) mode"
    )
    chat_p.add_argument(
        "--verbose", action="store_true", default=False, help="Show tool calls and verbose events"
    )

    # profile subcommand
    prof_p = subparsers.add_parser("profile", help="Manage citizen profile data")
    prof_sub = prof_p.add_subparsers(dest="profile_action", required=True)

    user_parent = argparse.ArgumentParser(add_help=False)
    user_parent.add_argument(
        "--user", default="default", help="User profile ID (default: 'default')"
    )

    # profile show
    prof_sub.add_parser(
        "show", parents=[user_parent], help="Show all profile fields with masked values"
    )

    # profile set <field> <value>
    prof_set_p = prof_sub.add_parser(
        "set", parents=[user_parent], help="Set and validate a profile field"
    )
    prof_set_p.add_argument("field", help="Field name from catalog")
    prof_set_p.add_argument("value", help="Value to set")

    # profile delete <field>
    prof_del_p = prof_sub.add_parser("delete", parents=[user_parent], help="Delete a profile field")
    prof_del_p.add_argument("field", help="Field name to delete")

    # serve subcommand
    serve_p = subparsers.add_parser("serve", help="Run FastAPI web server")
    serve_p.add_argument("--host", default="127.0.0.1", help="Host address (default: 127.0.0.1)")
    serve_p.add_argument("--port", type=int, default=8000, help="Port number (default: 8000)")
    serve_p.add_argument("--reload", action="store_true", default=False, help="Enable auto-reload")

    return parser


def main(argv: list[str] | None = None) -> int:
    """Main CLI entrypoint."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "chat":
        return asyncio.run(run_chat(args))
    elif args.command == "profile":
        if args.profile_action == "show":
            return cmd_profile_show(args)
        elif args.profile_action == "set":
            return cmd_profile_set(args)
        elif args.profile_action == "delete":
            return cmd_profile_delete(args)
        else:
            parser.print_help()
            return 2
    elif args.command == "serve":
        return cmd_serve(args)
    else:
        parser.print_help()
        return 2
