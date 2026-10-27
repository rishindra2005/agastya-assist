"""Portal playbook definition, loader, and intent matching."""

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any
import yaml

from agastya.profile.fields import FIELDS

REQUIRED_PLAYBOOK_KEYS = (
    "id",
    "portal",
    "title_en",
    "title_hi",
    "live_url",
    "fixture",
    "requires_login",
    "required_fields",
    "optional_fields",
    "field_hints",
    "steps",
    "stop_before",
    "keywords",
)


@dataclass
class Playbook:
    id: str
    portal: str
    title_en: str
    title_hi: str
    live_url: str  # real government URL
    fixture: str | None  # tests/fixtures/forms/<file>.html
    requires_login: bool
    required_fields: list[str]  # keys from agastya.profile.fields.FIELDS
    optional_fields: list[str]
    field_hints: dict[str, list[str]]  # profile key -> label/name/id substrings to match on the page
    steps: list[str]  # natural-language steps for the agent
    stop_before: list[str]  # button texts where the agent must hand over
    keywords: list[str]  # en + hi words for matching user intent


def _resolve_fixture_path(fixture: str) -> Path | None:
    """Resolve fixture path to an existing file if possible."""
    p = Path(fixture)
    if p.is_file():
        return p

    repo_root = Path(__file__).resolve().parent.parent.parent
    candidates = [
        repo_root / fixture,
        repo_root / "tests" / "fixtures" / "forms" / fixture,
        Path("tests/fixtures/forms") / fixture,
    ]
    for cand in candidates:
        if cand.is_file():
            return cand
    return None


def _validate_playbook_dict(data: Any, filepath: Path) -> None:
    """Validate raw playbook dictionary loaded from YAML."""
    if not isinstance(data, dict):
        raise ValueError(f"Playbook file '{filepath.name}' is not a valid dictionary")

    # 1. Required keys
    for req_key in REQUIRED_PLAYBOOK_KEYS:
        if req_key not in data:
            raise ValueError(
                f"Playbook file '{filepath.name}' is missing required key '{req_key}'"
            )

    # 2. Field keys exist in agastya.profile.fields.FIELDS
    required_fields = data.get("required_fields")
    if not isinstance(required_fields, list):
        raise ValueError(f"Playbook file '{filepath.name}' has invalid 'required_fields' (must be a list)")
    for f in required_fields:
        if f not in FIELDS:
            raise ValueError(
                f"Playbook file '{filepath.name}' contains unknown required field '{f}' not in FIELDS"
            )

    optional_fields = data.get("optional_fields")
    if not isinstance(optional_fields, list):
        raise ValueError(f"Playbook file '{filepath.name}' has invalid 'optional_fields' (must be a list)")
    for f in optional_fields:
        if f not in FIELDS:
            raise ValueError(
                f"Playbook file '{filepath.name}' contains unknown optional field '{f}' not in FIELDS"
            )

    field_hints = data.get("field_hints")
    if not isinstance(field_hints, dict):
        raise ValueError(f"Playbook file '{filepath.name}' has invalid 'field_hints' (must be a dict)")
    for f in field_hints.keys():
        if f not in FIELDS:
            raise ValueError(
                f"Playbook file '{filepath.name}' contains unknown field_hints key '{f}' not in FIELDS"
            )

    # 3. Fixture file exists if set
    fixture = data.get("fixture")
    if fixture is not None and str(fixture).strip():
        resolved = _resolve_fixture_path(str(fixture).strip())
        if not resolved:
            raise ValueError(
                f"Playbook file '{filepath.name}' references non-existent fixture file '{fixture}'"
            )


def load_playbooks(directory: Path | str | None = None) -> dict[str, Playbook]:
    """Load and validate all *.yaml playbooks from the given directory (default: agastya/portals/playbooks)."""
    if directory is None:
        dir_path = Path(__file__).resolve().parent / "playbooks"
    else:
        dir_path = Path(directory)

    if not dir_path.is_dir():
        raise ValueError(f"Playbooks directory does not exist: {dir_path}")

    playbooks: dict[str, Playbook] = {}
    yaml_files = sorted(dir_path.glob("*.yaml")) + sorted(dir_path.glob("*.yml"))
    # Remove duplicate stems if any
    unique_files: dict[str, Path] = {}
    for yf in yaml_files:
        if yf.name not in unique_files:
            unique_files[yf.name] = yf

    for filepath in unique_files.values():
        with open(filepath, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        _validate_playbook_dict(data, filepath)

        pb_id = str(data["id"]).strip()
        if pb_id in playbooks:
            raise ValueError(
                f"Playbook file '{filepath.name}' has duplicate id '{pb_id}'"
            )

        fixture_val = data["fixture"]
        if fixture_val is not None:
            fixture_val = str(fixture_val).strip()

        playbook = Playbook(
            id=pb_id,
            portal=str(data["portal"]),
            title_en=str(data["title_en"]),
            title_hi=str(data["title_hi"]),
            live_url=str(data["live_url"]),
            fixture=fixture_val,
            requires_login=bool(data["requires_login"]),
            required_fields=list(data["required_fields"]),
            optional_fields=list(data["optional_fields"]),
            field_hints={k: list(v) for k, v in data["field_hints"].items()},
            steps=[str(s) for s in data["steps"]],
            stop_before=[str(b) for b in data["stop_before"]],
            keywords=[str(k) for k in data["keywords"]],
        )
        playbooks[pb_id] = playbook

    return playbooks


def _normalize_text(text: str) -> str:
    """Normalize text: lowercase, normalize whitespace."""
    return " ".join(text.lower().strip().split())


def _tokenize(text: str) -> list[str]:
    """Tokenize text into unicode alphanumeric word tokens (supports English and Devanagari)."""
    return [t.lower() for t in re.findall(r"\w+", text, re.UNICODE) if t.strip()]


_FILLER_WORDS = {
    "chahiye",
    "hai",
    "karna",
    "ke",
    "ka",
    "ki",
    "ko",
    "me",
    "mein",
    "se",
    "par",
    "in",
    "the",
    "a",
    "an",
    "to",
    "for",
    "is",
    "of",
    "and",
    "or",
    "please",
    "help",
    "mujhe",
    "mera",
    "meri",
}


def score_playbook(query: str, playbook: Playbook) -> float:
    """Compute relevance score between user query and a playbook."""
    norm_query = _normalize_text(query)
    q_tokens = _tokenize(query)
    if not norm_query or not q_tokens:
        return 0.0

    score = 0.0

    # Weighted search targets
    targets: list[tuple[str, float]] = [
        (playbook.id, 12.0),
        (playbook.title_en, 10.0),
        (playbook.title_hi, 10.0),
    ]
    for kw in playbook.keywords:
        targets.append((kw, 15.0))

    matched_tokens: set[str] = set()

    for text, weight in targets:
        norm_text = _normalize_text(text)
        if not norm_text:
            continue
        text_tokens = _tokenize(text)

        # 1. Exact phrase match
        if norm_query == norm_text:
            score += weight * 60.0
        elif norm_text in norm_query:
            # e.g., keyword "naya pan card" is inside query "mujhe naya pan card chahiye"
            score += weight * (25.0 + len(norm_text))
        elif norm_query in norm_text:
            # e.g., query "refund status" is inside keyword "itr refund status"
            score += weight * (20.0 + len(norm_query))

        # 2. Token overlap
        for q_tok in q_tokens:
            if q_tok in text_tokens:
                matched_tokens.add(q_tok)
                token_weight = 1.0 if q_tok in _FILLER_WORDS else 12.0
                score += weight * token_weight

    # Multi-token overlap bonus
    content_matches = [t for t in matched_tokens if t not in _FILLER_WORDS]
    score += len(content_matches) * 25.0

    return score


def match_playbook(query: str, playbooks: dict[str, Playbook]) -> list[Playbook]:
    """Score playbooks against query and return matching playbooks, best first."""
    scored: list[tuple[float, Playbook]] = []
    for pb in playbooks.values():
        s = score_playbook(query, pb)
        if s > 0:
            scored.append((s, pb))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [pb for _, pb in scored]
