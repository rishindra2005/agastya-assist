"""Audio input/output primitives for Agastya Assist CLI and voice interactions."""

from __future__ import annotations

import asyncio
import logging
import shutil
import struct
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["record_until_enter", "play_wav", "fix_wav_header"]


def fix_wav_header(audio: bytes) -> bytes:
    """Fix RIFF and data chunk sizes in WAV audio bytes.

    When streaming WAV audio to stdout (e.g. arecord -t wav -), the recorder
    cannot seek backwards and writes 0x7FFFFFFF for both RIFF chunk size and
    data chunk size. This function parses the header and updates the chunk sizes
    to match the actual length of the byte stream.

    If the data is less than 44 bytes or not a RIFF WAVE format, it is returned
    unmodified.
    """
    if len(audio) < 44 or not audio.startswith(b"RIFF") or audio[8:12] != b"WAVE":
        return audio

    buf = bytearray(audio)
    total_len = len(buf)

    # Patch RIFF chunk size: total_len - 8 (bytes 4..8)
    struct.pack_into("<I", buf, 4, max(0, total_len - 8))

    # Iterate through subchunks starting at byte 12 to find the 'data' chunk
    pos = 12
    found_data = False
    while pos + 8 <= total_len:
        chunk_id = bytes(buf[pos : pos + 4])
        chunk_size = struct.unpack_from("<I", buf, pos + 4)[0]
        if chunk_id == b"data":
            actual_data_size = max(0, total_len - (pos + 8))
            struct.pack_into("<I", buf, pos + 4, actual_data_size)
            found_data = True
            break
        pos += 8 + chunk_size
        if chunk_size % 2 == 1:
            pos += 1

    if not found_data:
        # Fallback: search for b"data" chunk identifier
        idx = buf.find(b"data", 12)
        if idx != -1 and idx + 8 <= total_len:
            actual_data_size = max(0, total_len - (idx + 8))
            struct.pack_into("<I", buf, idx + 4, actual_data_size)

    return bytes(buf)


async def record_until_enter(prompt: str = "") -> bytes:
    """Record audio from the microphone until the user presses Enter.

    Spawns `arecord -q -f S16_LE -r 16000 -c 1 -t wav -` as an async subprocess,
    continuously reads audio frames from stdout, awaits user Enter via
    loop.run_in_executor(input), terminates arecord, and returns the fixed WAV bytes.

    Raises:
        RuntimeError: If `arecord` is not available on the system.
    """
    if not shutil.which("arecord"):
        raise RuntimeError("Audio recorder 'arecord' not found. Please install alsa-utils.")

    cmd = ["arecord", "-q", "-f", "S16_LE", "-r", "16000", "-c", "1", "-t", "wav", "-"]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    chunks: list[bytes] = []

    async def _read_stdout() -> None:
        if proc.stdout is None:
            return
        while True:
            chunk = await proc.stdout.read(4096)
            if not chunk:
                break
            chunks.append(chunk)

    reader_task = asyncio.create_task(_read_stdout())
    loop = asyncio.get_running_loop()

    try:
        if prompt:
            await loop.run_in_executor(None, input, prompt)
        else:
            await loop.run_in_executor(None, input)
    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        try:
            proc.terminate()
        except ProcessLookupError:
            pass

        try:
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except ProcessLookupError:
                pass

        await reader_task

    raw_wav = b"".join(chunks)
    return fix_wav_header(raw_wav)


async def play_wav(audio_data: bytes) -> None:
    """Play WAV audio bytes via `paplay` from stdin (fallback `aplay -q -`).

    Raises:
        RuntimeError: If neither `paplay` nor `aplay` is available, or if playback fails.
    """
    if not audio_data:
        return

    has_paplay = shutil.which("paplay") is not None
    has_aplay = shutil.which("aplay") is not None

    if not has_paplay and not has_aplay:
        raise RuntimeError("No audio player found (neither paplay nor aplay is available)")

    # Prefer paplay, fallback to aplay -q -
    cmd = ["paplay"] if has_paplay else ["aplay", "-q", "-"]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate(input=audio_data)

    if proc.returncode != 0 and has_paplay and has_aplay:
        # Fallback to aplay if paplay failed (e.g. PulseAudio server not running)
        cmd = ["aplay", "-q", "-"]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate(input=audio_data)

    if proc.returncode != 0:
        err_msg = stderr.decode(errors="replace").strip() if stderr else f"exit code {proc.returncode}"
        raise RuntimeError(f"Audio playback failed: {err_msg}")
