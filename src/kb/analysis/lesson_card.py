"""Transcript-direct, public-safe lesson display cards."""

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from llm_config import DEFAULT_LLM

from .core import _state

PROMPT_VERSION = "card-p7"
LESSON_TYPES = {"atm", "talk", "interview", "demonstration", "mixed", "other"}
POSITIONS = {"lying_on_back", "lying_on_side", "lying_on_front", "sitting", "standing", "kneeling", "all_fours", "other"}
SUPPORT_CONTEXTS = {"floor", "chair", "standing", "wall"}
APPLICATION_IDS = {
    "pain_comfort_recovery", "everyday_movement", "music_voice_performance",
    "sport_skilled_activity", "stress_rest_self_regulation",
    "learning_across_life", "creativity_meditation_self_knowledge",
}


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
    hosting_page_context: dict | str | None = None,
) -> dict:
    package = sys.modules[__package__]
    cache_key = lesson_card_cache_key(lesson_text, provider_key)
    cached = package.load_cached_analysis(cache_key)
    if cached:
        return {**cached, "cached": True}
    if isinstance(hosting_page_context, dict):
        context_text = json.dumps(hosting_page_context, ensure_ascii=False, default=str)
    else:
        context_text = str(hosting_page_context or "(missing)")
    prompt = _data_path("lesson_card.md").read_text().format(
        lesson_text=lesson_text,
        source_title=source_title or "(missing)",
        hosting_page_context=context_text[:6000],
    )
    schema = json.loads(_data_path("lesson_card.schema.json").read_text())
    llm = _state["_get_llm"](provider_key)
    t0 = time.time()
    result = llm.complete_structured_json(
        prompt=prompt,
        schema=schema,
        system=(
            "Produce conservative public metadata. Treat the transcript as authoritative "
            "for topical relevance; use hosting-page context only as secondary metadata."
        ),
    )
    summary = " ".join(str(result.get("summary") or "").split())
    if not summary or len(re.findall(r"\b[\w'-]+\b", summary)) > 60:
        raise ValueError("lesson card summary must contain 1–60 words")
    lesson_type = result.get("lesson_type")
    if lesson_type not in LESSON_TYPES:
        raise ValueError(f"invalid lesson_type: {lesson_type!r}")
    related = result.get("is_feldenkrais_related")
    if not isinstance(related, bool):
        raise TypeError(f"invalid is_feldenkrais_related: {related!r}")
    confidence = result.get("feldenkrais_relevance_confidence")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        raise TypeError(f"invalid feldenkrais_relevance_confidence: {confidence!r}")
    confidence = float(confidence)
    if not 0 <= confidence <= 1:
        raise ValueError(f"invalid feldenkrais_relevance_confidence: {confidence!r}")
    reason = " ".join(str(result.get("relevance_reason") or "").split())
    if not reason:
        raise ValueError("relevance_reason is required")
    starting = result.get("starting_position")
    if starting is not None and starting not in POSITIONS:
        raise ValueError(f"invalid starting_position: {starting!r}")
    primary_positions = [
        value for value in result.get("primary_positions", []) if value in POSITIONS
    ][:3]
    positions = [value for value in result.get("positions", []) if value in POSITIONS]
    positions = list(dict.fromkeys([*primary_positions, *positions]))
    supports = [value for value in result.get("support_context", []) if value in SUPPORT_CONTEXTS]
    language = str(result.get("primary_language") or "").lower()
    if not re.fullmatch(r"[a-z]{2,3}", language):
        raise ValueError(f"invalid primary_language: {language!r}")
    applications = result.get("applications") or []
    if len(applications) != len(APPLICATION_IDS):
        raise ValueError("lesson card must return every application exactly once")
    if {item.get("id") for item in applications} != APPLICATION_IDS:
        raise ValueError("lesson card returned invalid or duplicate application ids")
    for item in applications:
        if item.get("strength") not in {"intended", "mentioned", "absent"}:
            raise ValueError(f"invalid application strength: {item.get('strength')!r}")
    presenter_intent = " ".join(str(result.get("presenter_intent") or "").split())
    if not presenter_intent:
        raise ValueError("presenter_intent is required")
    payload = {
        "summary": summary,
        "suggested_title": result.get("suggested_title") or None,
        "is_feldenkrais_related": related,
        "feldenkrais_relevance_confidence": confidence,
        "relevance_reason": reason,
        "lesson_type": lesson_type,
        "starting_position": starting,
        "primary_positions": list(dict.fromkeys(primary_positions)),
        "positions": positions,
        "themes": list(dict.fromkeys(result.get("themes", [])))[:8],
        "support_context": list(dict.fromkeys(supports)),
        "equipment": list(dict.fromkeys(result.get("equipment", [])))[:8],
        "primary_language": language,
        "notable_feature": result.get("notable_feature") or None,
        "audience_notes": result.get("audience_notes") or None,
        "presenter_intent": presenter_intent,
        "intent_evidence": list(dict.fromkeys(result.get("intent_evidence") or []))[:2],
        "applications": applications,
        "prompt_version": PROMPT_VERSION,
        "model": getattr(llm, "model", None) or (provider_key or DEFAULT_LLM),
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "elapsed": round(time.time() - t0, 1),
        "usage": llm.last_usage or {},
    }
    package.save_cached_analysis(cache_key, payload)
    return {**payload, "cached": False}
