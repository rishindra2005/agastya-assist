"""WAV audio utilities and text splitting for Agastya Assist voice pipeline."""

import io
import re
import wave


def concat_wavs(wavs: list[bytes]) -> bytes:
    """Concatenate multiple WAV audio byte strings into a single WAV byte string.

    All input WAV files must have matching parameters (nchannels, sampwidth,
    framerate, comptype). Raises ValueError if parameters differ.

    If wavs is empty, returns b"".
    If wavs has a single item, validates that it is a valid WAV and returns it.
    """
    if not wavs:
        return b""
    if len(wavs) == 1:
        with wave.open(io.BytesIO(wavs[0]), "rb"):
            pass
        return wavs[0]

    with wave.open(io.BytesIO(wavs[0]), "rb") as r:
        params = r.getparams()
        frames = [r.readframes(r.getnframes())]

    expected = (params.nchannels, params.sampwidth, params.framerate, params.comptype)

    for idx, w in enumerate(wavs[1:], start=1):
        with wave.open(io.BytesIO(w), "rb") as r:
            p = r.getparams()
            actual = (p.nchannels, p.sampwidth, p.framerate, p.comptype)
            if actual != expected:
                raise ValueError(
                    f"WAV parameters at index {idx} do not match: expected {expected}, got {actual}"
                )
            frames.append(r.readframes(r.getnframes()))

    out_buf = io.BytesIO()
    with wave.open(out_buf, "wb") as w:
        w.setparams(params)
        w.writeframes(b"".join(frames))

    return out_buf.getvalue()


def split_text(text: str, max_chars: int) -> list[str]:
    """Split text into chunks of at most max_chars length.

    Splits primarily on sentence boundaries (।, ., ?, !), keeping delimiters with the sentence.
    If a sentence exceeds max_chars, it is split on spaces.
    If an unbroken word exceeds max_chars, it is split at character limits.
    Greedily packs sentence/phrase chunks up to max_chars without exceeding the limit.
    Never returns empty chunks.
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    # Delimiters: । (Hindi danda \u0964), . ? !
    parts = re.split(r"([।\.?!]+)", text)
    sentences: list[str] = []
    for i in range(0, len(parts) - 1, 2):
        s = (parts[i] + parts[i + 1]).strip()
        if s:
            sentences.append(s)
    if len(parts) % 2 == 1 and parts[-1].strip():
        sentences.append(parts[-1].strip())

    if not sentences:
        sentences = [text]

    # If any sentence is longer than max_chars, split on spaces
    refined: list[str] = []
    for s in sentences:
        if len(s) <= max_chars:
            refined.append(s)
        else:
            words = s.split()
            current = ""
            for w in words:
                if len(w) > max_chars:
                    if current:
                        refined.append(current)
                        current = ""
                    for j in range(0, len(w), max_chars):
                        refined.append(w[j : j + max_chars])
                else:
                    if not current:
                        current = w
                    elif len(current) + 1 + len(w) <= max_chars:
                        current += " " + w
                    else:
                        refined.append(current)
                        current = w
            if current:
                refined.append(current)

    # Greedily pack refined pieces into chunks up to max_chars
    chunks: list[str] = []
    curr_chunk = ""
    for item in refined:
        if not curr_chunk:
            curr_chunk = item
        elif len(curr_chunk) + 1 + len(item) <= max_chars:
            curr_chunk += " " + item
        else:
            chunks.append(curr_chunk)
            curr_chunk = item
    if curr_chunk:
        chunks.append(curr_chunk)

    return [c.strip() for c in chunks if c.strip()]
