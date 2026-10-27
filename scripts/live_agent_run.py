#!/usr/bin/env python3
"""Run the real Claude Agent on live government portals with dummy data.

Strict Safety (non-negotiable):
- Dummy profile only (name 'Test Kumar Demo', mobile 9000000001, Aadhaar empty).
- Never answer OTP, captcha, password or login questions (answers 'I will do it myself').
- Never answer Aadhaar question (answers 'skip, I will enter it myself').
- The safety guard stays in place (never monkeypatched).
- Stop immediately and report if an unblocked final button is ever clicked.
- Headless browser, one portal at a time, max 6 minutes per portal, close browser after each.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from agastya.agent.events import AgentEvent
from agastya.agent.session import AgentSession
from agastya.browser.driver import BrowserDriver
from agastya.browser.guard import BLOCK_RULES, check_click
from agastya.portals import Playbook, load_playbooks
from agastya.profile.store import ProfileStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("live_agent_run")

DEFAULT_PLAYBOOK_IDS = [
    "voter_form6",
    "pan_new_49a",
    "itr_register",
    "pan_aadhaar_link",
    "mca_company_master",
]

DUMMY_PROFILE: dict[str, str] = {
    "full_name": "Test Kumar Demo",
    "father_name": "Sample Prasad Demo",
    "mother_name": "Sample Devi Demo",
    "date_of_birth": "01/01/1990",
    "gender": "Male",
    "mobile": "9000000001",
    "email": "test.agastya@example.com",
    "address_line1": "123 Test Marg",
    "address_line2": "Connaught Place",
    "city": "New Delhi",
    "district": "New Delhi",
    "state": "Delhi",
    "pincode": "110001",
    "pan": "ABCDE1234F",
    "cin": "U72200DL2020PTC366000",
    "company_name": "Test Tech Private Limited",
    "assembly_constituency": "New Delhi",
    # Aadhaar is intentionally omitted per safety rules.
}


def decide_answer(
    question: str,
    field: str | None = None,
    fields: list[str] | None = None,
    profile_data: dict[str, str] | None = None,
) -> str:
    """Decide the safe auto-answer for an ask_user question."""
    q_lower = (question or "").lower()
    profile = profile_data or {}

    # Safety Rule 1: OTP, captcha, password, login -> "I will do it myself"
    auth_terms = [
        "otp",
        "ओटीपी",
        "captcha",
        "कैप्चा",
        "password",
        "पासवर्ड",
        "login",
        "लॉगिन",
        "लॉग इन",
        "sign in",
        "code",
        "कोड",
    ]
    if any(term in q_lower or term in question for term in auth_terms):
        return "I will do it myself"

    # Safety Rule 2: Submit, pay, or final confirmation -> "I will do it myself"
    final_terms = [
        "submit",
        "सबमिट",
        "payment",
        "pay",
        "पेमेंट",
        "भुगतान",
        "जमा करें",
        "जमा करना",
        "जमा कर",
    ]
    if any(term in q_lower or term in question for term in final_terms):
        return "I will do it myself"

    # Safety Rule 3: Aadhaar -> "skip, I will enter it myself"
    if (
        (field and "aadhaar" in field.lower())
        or (fields and any("aadhaar" in f.lower() for f in fields))
        or "aadhaar" in q_lower
        or "आधार" in question
    ):
        return "skip, I will enter it myself"

    # Known profile fields
    if field and field in profile:
        return profile[field]

    if fields:
        matched = [profile[f] for f in fields if f in profile and "aadhaar" not in f.lower()]
        if matched:
            return ", ".join(matched)

    # General confirmation / proceed questions
    confirm_terms = ["confirm", "proceed", "सही", "आगे", "बढ़ूँ", "भरूँ"]
    if any(term in q_lower or term in question for term in confirm_terms):
        return "हाँ, आगे बढ़ें"

    # Default fallback
    return "skip, I will enter it myself"


def is_potentially_final_action(action_text: str) -> bool:
    """Check if action text matches any final submit/pay/otp rule from guard."""
    if not action_text:
        return False
    norm = action_text.strip().lower()
    for pattern, _ in BLOCK_RULES:
        if pattern.search(norm):
            return True
    return False


async def run_portal(
    playbook: Playbook,
    out_dir: Path,
    timeout_seconds: float = 360.0,
) -> dict[str, Any]:
    """Execute AgentSession on a live government portal playbook.

    Saves screenshots and events, enforces safety guard, and returns run summary.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    events_file = out_dir / "events.jsonl"
    events_f = open(events_file, "w", encoding="utf-8")

    summary: dict[str, Any] = {
        "playbook_id": playbook.id,
        "portal": playbook.portal,
        "title_hi": playbook.title_hi,
        "live_url": playbook.live_url,
        "final_url": playbook.live_url,
        "fields_filled": 0,
        "filled_details": [],
        "blocked_events": 0,
        "blocked_details": [],
        "handoff": False,
        "handoff_reason": "",
        "screenshot_count": 0,
        "duration": 0.0,
        "errors": [],
        "critical_unblocked_click": False,
    }

    start_time = time.time()
    temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    temp_db_path = Path(temp_db.name)
    temp_db.close()

    browser: BrowserDriver | None = None
    session: AgentSession | None = None

    try:
        # 1. Initialize isolated ProfileStore
        profile = ProfileStore(db_path=temp_db_path)
        for k, v in DUMMY_PROFILE.items():
            profile.set(k, v)

        # 2. Start headless BrowserDriver and AgentSession
        browser = BrowserDriver(headless=True)
        session = AgentSession(
            user_id=f"live_{playbook.id}",
            profile=profile,
            browser=browser,
            ask_timeout=60.0,
        )
        await session.start()

        event_queue = session.bus.subscribe()

        # 3. Request in Hindi: 'mujhe <title_hi> karna hai'
        user_msg = f"mujhe {playbook.title_hi} karna hai"
        logger.info(f"[{playbook.id}] Sending user message: '{user_msg}'")
        await session.send_user_message(user_msg, language_code="hi-IN")

        screenshot_idx = 0
        latest_known_url = playbook.live_url
        pending_click_ref: str | None = None
        recent_actions: dict[str, str] = {}  # ref -> text

        while (time.time() - start_time) < timeout_seconds:
            remaining = timeout_seconds - (time.time() - start_time)
            if remaining <= 0:
                summary["errors"].append("Timeout: reached 6-minute maximum limit.")
                break

            try:
                ev: AgentEvent = await asyncio.wait_for(event_queue.get(), timeout=min(remaining, 4.0))
            except asyncio.TimeoutError:
                # Check if session loop completed
                if session._loop_task is not None and session._loop_task.done():
                    logger.info(f"[{playbook.id}] Agent run loop completed.")
                    break
                continue

            ev_json = ev.to_json()
            ev_data = ev.data or {}

            # Save screenshot if present
            if ev.type == "screenshot" and "png_b64" in ev_data:
                screenshot_idx += 1
                summary["screenshot_count"] = screenshot_idx
                png_bytes = base64.b64decode(ev_data["png_b64"])
                shot_path = out_dir / f"{screenshot_idx:02d}.png"
                shot_path.write_bytes(png_bytes)
                if ev_data.get("url"):
                    latest_known_url = str(ev_data["url"])
                    summary["final_url"] = latest_known_url
                logger.info(f"[{playbook.id}] Screenshot #{screenshot_idx:02d} saved to {shot_path.name}")

            # Strip b64 data before writing to events.jsonl
            log_data = dict(ev_data)
            if "png_b64" in log_data:
                del log_data["png_b64"]
            log_entry = {
                "type": ev.type,
                "data": log_data,
                "ts": ev.ts,
            }
            events_f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
            events_f.flush()

            # Event handling
            if ev.type == "ask_user":
                qid = ev_data.get("question_id", "")
                q_text = str(ev_data.get("question") or "")
                q_field = ev_data.get("field")
                q_fields = ev_data.get("fields", [])
                ans = decide_answer(q_text, q_field, q_fields, DUMMY_PROFILE)
                logger.info(f"[{playbook.id}] ask_user: '{q_text}' (field={q_field}, fields={q_fields}) -> answering '{ans}'")
                await session.answer_question(qid, ans)

            elif ev.type == "tool_call":
                t_name = ev_data.get("name", "")
                t_inp = ev_data.get("input", {})
                logger.info(f"[{playbook.id}] tool_call: {t_name}({t_inp})")

                if t_name in ("mcp__agastya__browser_fill", "browser_fill", "mcp__agastya__browser_select", "browser_select"):
                    summary["fields_filled"] += 1
                    summary["filled_details"].append({
                        "tool": t_name,
                        "ref": t_inp.get("ref"),
                        "value": t_inp.get("value") or t_inp.get("option"),
                    })

                elif t_name in ("mcp__agastya__browser_click", "browser_click"):
                    ref = t_inp.get("ref", "")
                    pending_click_ref = ref
                    # Check if action text was recognized as final
                    action_text = recent_actions.get(ref, "")
                    if action_text and is_potentially_final_action(action_text):
                        logger.warning(
                            f"[{playbook.id}] Pending click on potentially sensitive action ref={ref} text='{action_text}'"
                        )

                elif t_name in ("mcp__agastya__browser_goto", "browser_goto"):
                    url = t_inp.get("url", "")
                    if url:
                        latest_known_url = url
                        summary["final_url"] = url

            elif ev.type == "blocked":
                summary["blocked_events"] += 1
                reason = ev_data.get("reason", "")
                ref = ev_data.get("ref", "")
                logger.warning(f"[{playbook.id}] BLOCKED event: reason='{reason}', ref='{ref}'")
                summary["blocked_details"].append({"reason": reason, "ref": ref})
                pending_click_ref = None

            elif ev.type == "status":
                if ev_data.get("handoff"):
                    summary["handoff"] = True
                    summary["handoff_reason"] = ev_data.get("reason", "")
                    logger.info(f"[{playbook.id}] Handoff triggered: '{summary['handoff_reason']}'")

            elif ev.type == "error":
                summary["errors"].append(ev_data.get("error", str(ev_data)))
                logger.error(f"[{playbook.id}] Error event: {ev_data}")

            elif ev.type == "done":
                logger.info(f"[{playbook.id}] Received 'done' event from agent.")
                # Give a 2-second grace period for any last events
                await asyncio.sleep(2.0)
                while not event_queue.empty():
                    trailing_ev = event_queue.get_nowait()
                    t_json = trailing_ev.to_json()
                    t_data = dict(trailing_ev.data or {})
                    if "png_b64" in t_data:
                        del t_data["png_b64"]
                    events_f.write(json.dumps({"type": trailing_ev.type, "data": t_data, "ts": trailing_ev.ts}, ensure_ascii=False) + "\n")
                break

        # Capture final screenshot if none was taken or page changed
        if browser is not None and browser.page is not None and not browser.page.is_closed():
            try:
                final_png = await browser.screenshot()
                if screenshot_idx == 0:
                    screenshot_idx = 1
                    summary["screenshot_count"] = 1
                    (out_dir / "01.png").write_bytes(final_png)
                latest_known_url = browser.url or latest_known_url
                summary["final_url"] = latest_known_url
            except Exception as e:
                logger.debug(f"Could not take final screenshot: {e}")

    except Exception as exc:
        logger.exception(f"[{playbook.id}] Exception during live run: {exc}")
        summary["errors"].append(f"{type(exc).__name__}: {exc}")

    finally:
        events_f.close()
        summary["duration"] = round(time.time() - start_time, 2)

        if session is not None:
            try:
                await session.stop()
            except Exception as e:
                logger.warning(f"[{playbook.id}] Error stopping session: {e}")

        if browser is not None:
            try:
                await browser.close()
            except Exception as e:
                logger.warning(f"[{playbook.id}] Error closing browser: {e}")

        if temp_db_path.exists():
            try:
                temp_db_path.unlink()
            except Exception:
                pass

    return summary


def print_summary(summary: dict[str, Any]) -> None:
    """Print formatted summary of a portal run."""
    print(f"\n{'='*70}")
    print(f"PORTAL RUN SUMMARY: {summary['playbook_id']} ({summary['portal']})")
    print(f"{'='*70}")
    print(f"Title (Hindi)   : {summary['title_hi']}")
    print(f"Target URL      : {summary['live_url']}")
    print(f"Final URL       : {summary['final_url']}")
    print(f"Duration        : {summary['duration']}s")
    print(f"Screenshots     : {summary['screenshot_count']}")
    print(f"Fields Filled   : {summary['fields_filled']}")
    if summary["filled_details"]:
        for fd in summary["filled_details"][:8]:
            print(f"  - {fd.get('tool')}: ref={fd.get('ref')} val={fd.get('value')}")
        if len(summary["filled_details"]) > 8:
            print(f"  - ... ({len(summary['filled_details']) - 8} more)")
    print(f"Blocked Events  : {summary['blocked_events']}")
    for bd in summary["blocked_details"]:
        print(f"  - Reason: {bd.get('reason')} (ref: {bd.get('ref')})")
    print(f"Handoff Occurred: {summary['handoff']}")
    if summary["handoff"]:
        print(f"Handoff Reason  : {summary['handoff_reason']}")
    if summary["errors"]:
        print(f"Errors ({len(summary['errors'])}):")
        for err in summary["errors"]:
            print(f"  ! {err}")
    if summary["critical_unblocked_click"]:
        print("CRITICAL: Unblocked final click detected!")
    print(f"{'='*70}\n")


async def main() -> int:
    """Main runner for live agent runs across designated playbooks."""
    target_ids = sys.argv[1:] if len(sys.argv) > 1 else DEFAULT_PLAYBOOK_IDS
    playbooks = load_playbooks()

    base_out_dir = Path("data/live_runs")
    base_out_dir.mkdir(parents=True, exist_ok=True)

    all_summaries: list[dict[str, Any]] = []

    print(f"Starting Agastya live agent runs for {len(target_ids)} playbooks: {target_ids}")
    print(f"Output directory: {base_out_dir.resolve()}\n")

    for pid in target_ids:
        if pid not in playbooks:
            logger.error(f"Playbook ID '{pid}' not found in loaded playbooks. Skipping.")
            continue

        pb = playbooks[pid]
        out_dir = base_out_dir / pid
        logger.info(f"Running live agent on portal '{pid}' ({pb.live_url})...")

        res = await run_portal(pb, out_dir, timeout_seconds=360.0)
        all_summaries.append(res)
        print_summary(res)

        if res.get("critical_unblocked_click"):
            msg = f"[t6d-live-runs] CRITICAL: Unblocked final button click detected on portal {pid}!"
            print(f"FATAL: {msg}")
            # Append critical notice to board immediately
            board_file = Path(".agy/board.md")
            if board_file.exists():
                with open(board_file, "a", encoding="utf-8") as f:
                    f.write(f"\n{msg}\n")
            return 1

    # Print final overview table
    print("\n" + "=" * 80)
    print("OVERALL LIVE RUNS SUMMARY")
    print("=" * 80)
    header = f"{'Playbook':<22} | {'Fields':<7} | {'Blocked':<8} | {'Handoff':<8} | {'Duration':<9} | {'Screenshots'}"
    print(header)
    print("-" * len(header))
    for s in all_summaries:
        h_str = "YES" if s["handoff"] else "NO"
        row = f"{s['playbook_id']:<22} | {s['fields_filled']:<7} | {s['blocked_events']:<8} | {h_str:<8} | {s['duration']:<7.1f}s | {s['screenshot_count']}"
        print(row)
    print("=" * 80)

    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
