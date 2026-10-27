#!/usr/bin/env python3
"""Latency probe measuring STT, TTS time-to-first-audio, and real agent run on PAN fixture."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import json
import logging
from pathlib import Path
import tempfile
import time
from typing import Any

from agastya.agent.events import AgentEvent, EventBus
from agastya.agent.session import AgentSession
from agastya.config import get_settings
from agastya.profile.store import ProfileStore
from agastya.voice.pipeline import VoicePipeline
from agastya.voice.stt import SarvamSTT
from agastya.voice.tts import SarvamTTS

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("latency_probe")

PAN_PROFILE_DATA = {
    "full_name": "Ravi Kumar Sharma",
    "father_name": "Suresh Kumar Sharma",
    "mother_name": "Sunita Sharma",
    "date_of_birth": "15/08/1995",
    "gender": "male",
    "mobile": "9876543210",
    "email": "ravi.sharma@example.com",
    "aadhaar": "234567890123",
    "address_line1": "Flat 12, Shanti Apartments",
    "address_line2": "MG Road",
    "city": "Lucknow",
    "district": "Lucknow",
    "state": "Uttar Pradesh",
    "pincode": "226001",
    "occupation": "salaried",
}

DEFAULT_HINDI_REPLY = (
    "नमस्ते, मैं आपका पैन कार्ड आवेदन भरने में मदद कर रहा हूँ। "
    "कृपया अपने विवरण की जाँच करें और पुष्टि करें।"
)


@dataclass
class ProbeResults:
    stt_seconds: float
    stt_text: str
    tts_ttfa_seconds: float
    tts_total_seconds: float
    tts_chunks_count: int
    agent_first_text_seconds: float | None
    agent_tool_calls_count: int
    agent_tool_breakdown: dict[str, int]
    agent_total_turn_seconds: float
    agent_fields_filled_count: int
    agent_handoff: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "stt_seconds": round(self.stt_seconds, 3),
            "stt_text": self.stt_text,
            "tts_ttfa_seconds": round(self.tts_ttfa_seconds, 3),
            "tts_total_seconds": round(self.tts_total_seconds, 3),
            "tts_chunks_count": self.tts_chunks_count,
            "agent_first_text_seconds": round(self.agent_first_text_seconds, 3) if self.agent_first_text_seconds is not None else None,
            "agent_tool_calls_count": self.agent_tool_calls_count,
            "agent_tool_breakdown": self.agent_tool_breakdown,
            "agent_total_turn_seconds": round(self.agent_total_turn_seconds, 3),
            "agent_fields_filled_count": self.agent_fields_filled_count,
            "agent_handoff": self.agent_handoff,
        }

    def markdown_table(self, title: str = "Benchmark Results") -> str:
        lines = [
            f"### {title}",
            "",
            "| Metric | Value |",
            "|---|---|",
            f"| STT Latency (`sample_hi.wav`) | {self.stt_seconds:.3f} s |",
            f"| TTS Time-to-First-Audio (TTFA) | {self.tts_ttfa_seconds:.3f} s |",
            f"| TTS Total Synthesis Time | {self.tts_total_seconds:.3f} s ({self.tts_chunks_count} chunks) |",
            f"| Agent Time-to-First-Assistant-Text | {f'{self.agent_first_text_seconds:.3f} s' if self.agent_first_text_seconds is not None else 'N/A'} |",
            f"| Agent Tool Calls Count | {self.agent_tool_calls_count} |",
            f"| Agent Tool Breakdown | {json.dumps(self.agent_tool_breakdown)} |",
            f"| Agent Fields Filled Count | {self.agent_fields_filled_count} |",
            f"| Agent Handoff Emitted | {'Yes' if self.agent_handoff else 'No'} |",
            f"| Agent Total Turn Time | {self.agent_total_turn_seconds:.3f} s |",
            "",
        ]
        return "\n".join(lines)


async def measure_stt(sample_path: Path) -> tuple[float, str]:
    logger.info("Measuring STT latency on %s ...", sample_path)
    audio_bytes = sample_path.read_bytes()
    stt = SarvamSTT()
    t0 = time.perf_counter()
    transcript = await stt.transcribe(audio_bytes, language_code="unknown")
    elapsed = time.perf_counter() - t0
    logger.info("STT finished in %.3f s: %r", elapsed, transcript.text)
    return elapsed, transcript.text


async def measure_tts(hindi_text: str = DEFAULT_HINDI_REPLY) -> tuple[float, float, int]:
    logger.info("Measuring TTS TTFA for 2-sentence Hindi text ...")
    session = AgentSession()
    q = session.bus.subscribe()
    pipeline = VoicePipeline(session=session, auto_speak=True)
    await pipeline.start()

    t0 = time.perf_counter()
    ttfa: float | None = None
    chunks_count = 0
    t_total: float | None = None

    session.bus.emit("assistant_text", text=hindi_text, language_code="hi-IN")

    try:
        while True:
            ev = await asyncio.wait_for(q.get(), timeout=30.0)
            if ev.type == "tts":
                chunks_count += 1
                if ttfa is None:
                    ttfa = time.perf_counter() - t0
                    logger.info("TTS first audio received in %.3f s", ttfa)
                if ev.data.get("final", False):
                    t_total = time.perf_counter() - t0
                    break
    finally:
        await pipeline.stop()
        session.bus.unsubscribe(q)

    assert ttfa is not None, "Did not receive any TTS audio event"
    if t_total is None:
        t_total = time.perf_counter() - t0
    logger.info("TTS total time: %.3f s across %d chunks", t_total, chunks_count)
    return ttfa, t_total, chunks_count


async def measure_agent_pan_run(timeout: float = 300.0) -> tuple[float | None, int, dict[str, int], float, int, bool]:
    logger.info("Measuring real agent run on PAN 49A fixture ...")
    forms_dir = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "forms"
    pan_fixture_path = forms_dir / "pan_49a.html"
    if not pan_fixture_path.exists():
        raise FileNotFoundError(f"Fixture not found at {pan_fixture_path}")
    pan_fixture_uri = pan_fixture_path.resolve().as_uri()

    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "test_pan.db"
        profile = ProfileStore(db_path=db_path)
        for k, v in PAN_PROFILE_DATA.items():
            profile.set(k, v)

        session = AgentSession(profile=profile)
        await session.start()
        q = session.bus.subscribe()

        user_msg = (
            f"मुझे नया पैन कार्ड बनवाना है। "
            f"(Test mode: open the local copy {pan_fixture_uri} instead of live_url.)"
        )

        t0 = time.perf_counter()
        await session.send_user_message(user_msg, language_code="hi-IN")

        first_text_time: float | None = None
        tool_breakdown: dict[str, int] = {}
        tool_calls_count = 0
        handoff_emitted = False
        fields_filled = 0

        try:
            while time.perf_counter() - t0 < timeout:
                remaining = timeout - (time.perf_counter() - t0)
                if remaining <= 0:
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=min(remaining, 5.0))
                except asyncio.TimeoutError:
                    if session._loop_task is not None and session._loop_task.done():
                        break
                    continue

                if ev.type == "assistant_text" and first_text_time is None:
                    first_text_time = time.perf_counter() - t0
                    logger.info("Agent first assistant_text in %.3f s", first_text_time)

                elif ev.type == "tool_call":
                    tool_calls_count += 1
                    tname = ev.data.get("name", "unknown")
                    tool_breakdown[tname] = tool_breakdown.get(tname, 0) + 1
                    logger.info("Tool call #%d: %s", tool_calls_count, tname)

                elif ev.type == "ask_user":
                    d = ev.data
                    qid = d.get("question_id", "")
                    q_field = d.get("field")
                    if q_field == "mother_name":
                        ans = "मेरी माँ का नाम सुनीता शर्मा है"
                    else:
                        ans = "हाँ, सब सही है"
                    await session.answer_question(qid, ans)

                elif ev.type == "status" and ev.data.get("handoff") is True:
                    handoff_emitted = True
                    logger.info("Handoff event received.")

                elif ev.type == "done":
                    logger.info("Agent done event received.")
                    break

            total_turn_time = time.perf_counter() - t0

            # Count filled fields in browser page
            if session.browser and session.browser.page:
                try:
                    vals = await session.browser.page.evaluate(
                        """() => Object.fromEntries([...document.querySelectorAll('input,select,textarea')]
                            .filter(e => e.name || e.id)
                            .map(e => [e.name || e.id, e.type==='checkbox'||e.type==='radio' ? (e.checked ? e.value : '') : e.value])
                            .filter(([k,v]) => v))"""
                    )
                    fields_filled = len(vals)
                except Exception as e:
                    logger.warning("Could not count filled fields on page: %s", e)

        finally:
            session.bus.unsubscribe(q)
            await session.stop()

    return (
        first_text_time,
        tool_calls_count,
        tool_breakdown,
        total_turn_time,
        fields_filled,
        handoff_emitted,
    )


async def run_probe(skip_agent: bool = False) -> ProbeResults:
    sample_path = Path("tests/fixtures/audio/sample_hi.wav")
    stt_seconds, stt_text = await measure_stt(sample_path)
    tts_ttfa, tts_total, tts_chunks = await measure_tts()

    if skip_agent:
        first_text, tool_count, breakdown, turn_time, filled, handoff = (
            None, 0, {}, 0.0, 0, False
        )
    else:
        first_text, tool_count, breakdown, turn_time, filled, handoff = (
            await measure_agent_pan_run()
        )

    results = ProbeResults(
        stt_seconds=stt_seconds,
        stt_text=stt_text,
        tts_ttfa_seconds=tts_ttfa,
        tts_total_seconds=tts_total,
        tts_chunks_count=tts_chunks,
        agent_first_text_seconds=first_text,
        agent_tool_calls_count=tool_count,
        agent_tool_breakdown=breakdown,
        agent_total_turn_seconds=turn_time,
        agent_fields_filled_count=filled,
        agent_handoff=handoff,
    )
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Agastya Latency Probe")
    parser.add_argument("--skip-agent", action="store_true", help="Skip agent PAN fixture run")
    parser.add_argument("--json", action="store_true", help="Output JSON only")
    args = parser.parse_args()

    results = asyncio.run(run_probe(skip_agent=args.skip_agent))

    if args.json:
        print(json.dumps(results.to_dict(), indent=2))
    else:
        print("\n" + results.markdown_table("Agastya Latency Probe Results"))


if __name__ == "__main__":
    main()
