"""Transcript-direct, public-safe lesson display cards."""

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from llm_config import DEFAULT_LLM

from .core import _state

PROMPT_VERSION = "card-p2"
LESSON_TYPES = {"atm", "talk", "interview", "demonstration", "mixed"}
POSITIONS = {"lying_on_back", "lying_on_side", "lying_on_front", "sitting", "standing", "kneeling", "all_fours", "other"}
SUPPORT_CONTEXTS = {"floor", "chair", "standing", "wall"}


def _data_path(name: str) -> Path:
    configured = _state["_data_dir"] / name
    return configured if configured.exists() else Path(__file__).resolve().parents[3] / "data" / name


def lesson_card_cache_key(lesson_text: str, provider_key: str | None = None) -> str:
    package = sys.modules[__package__]
    suffix = f":lesson_card:{PROMPT_VERSION}"
    if provider_key and provider_key != DEFAULT_LLM:
        suffix += f":{provider_key}"
    return package.get_cache_key(lesson_text.strip() + suffix)


def run_lesson_card(
    lesson_text: str,
    provider_key: str | None = None,
    source_title: str | None = None,
) -> dict:
    package = sys.modules[__package__]
    cache_key = lesson_card_cache_key(lesson_text, provider_key)
    cached = package.load_cached_analysis(cache_key)
    if cached:
        return {**cached, "cached": True}
    prompt = _data_path("lesson_card.md").read_text().format(
        lesson_text=lesson_text,
        source_title=source_title or "(missing)",
    )
    schema = json.loads(_data_path("lesson_card.schema.json").read_text())
    llm = _state["_get_llm"](provider_key)
    t0 = time.time()
    result = llm.complete_structured_json(
        prompt=prompt,
        schema=schema,
        system="Produce conservative public metadata from the supplied transcript only.",
    )
    summary = " ".join(str(result.get("summary") or "").split())
    if not summary or len(re.findall(r"\b[\w'-]+\b", summary)) > 60:
        raise ValueError("lesson card summary must contain 1–60 words")
    lesson_type = result.get("lesson_type")
    if lesson_type not in LESSON_TYPES:
        raise ValueError(f"invalid lesson_type: {lesson_type!r}")
    starting = result.get("starting_position")
    if starting is not None and starting not in POSITIONS:
        raise ValueError(f"invalid starting_position: {starting!r}")
    positions = [value for value in result.get("positions", []) if value in POSITIONS]
    supports = [value for value in result.get("support_context", []) if value in SUPPORT_CONTEXTS]
    language = str(result.get("primary_language") or "").lower()
    if not re.fullmatch(r"[a-z]{2,3}", language):
        raise ValueError(f"invalid primary_language: {language!r}")
    payload = {
        "summary": summary,
        "suggested_title": result.get("suggested_title") or None,
        "lesson_type": lesson_type,
        "starting_position": starting,
        "positions": list(dict.fromkeys(positions)),
        "themes": list(dict.fromkeys(result.get("themes", [])))[:8],
        "support_context": list(dict.fromkeys(supports)),
        "equipment": list(dict.fromkeys(result.get("equipment", [])))[:8],
        "primary_language": language,
        "notable_feature": result.get("notable_feature") or None,
        "audience_notes": result.get("audience_notes") or None,
        "prompt_version": PROMPT_VERSION,
        "model": getattr(llm, "model", None) or (provider_key or DEFAULT_LLM),
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "elapsed": round(time.time() - t0, 1),
        "usage": llm.last_usage or {},
    }
    package.save_cached_analysis(cache_key, payload)
    return {**payload, "cached": False}
