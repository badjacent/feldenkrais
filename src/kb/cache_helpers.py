"""Cache utilities for analysis pipeline.

Provides cache key generation and load/save helpers that wrap
the Database cache interface. Also contains build_summary_from_condensed
(single source of truth, used by both run_pipeline_batch.py and build_embeddings.py).
"""

import hashlib

_db = None


def init(db):
    """Initialize with a Database instance (or None)."""
    global _db
    _db = db


def get_cache_key(text: str) -> str:
    """Generate a cache key from lesson text."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def load_cached_analysis(cache_key: str) -> dict | None:
    """Load cached analysis result from database."""
    if _db:
        return _db.get_cache(cache_key)
    return None


def save_cached_analysis(cache_key: str, data: dict) -> None:
    """Save analysis result to database cache."""
    if _db:
        _db.set_cache(cache_key, data)


def profile_result_key(text: str, suffix: str, profile: dict) -> str:
    """Cache key of a step result produced under an analysis profile."""
    from db.analysis_profiles import profile_cache_suffix

    return get_cache_key(text + suffix + profile_cache_suffix(profile))


def load_profile_result(text: str, suffix: str, profile: dict) -> tuple[str, dict | None]:
    """Load one profile's result for a step. Never falls back to another profile."""
    key = profile_result_key(text, suffix, profile)
    return key, load_cached_analysis(key)


def save_prompt_io(cache_key: str, prompt: str, system: str, schema: dict | None = None) -> None:
    """Save the prompt/system/schema used to produce a cached result.

    Stored under ``{cache_key}:prompt`` so it lives next to the result.
    """
    if _db:
        payload: dict = {"prompt": prompt, "system": system}
        if schema:
            payload["schema"] = schema
        _db.set_cache(cache_key + ":prompt", payload)


def build_summary_from_condensed(condensed: dict, title: str) -> str:
    """Build a summary string from condensed card data for embedding."""
    parts = [title]
    for seg in condensed.get("segments", []):
        setting = seg.get("setting", {})
        if setting:
            if setting.get("title"):
                parts.append(f"Setting: {setting['title']}")
            if setting.get("somatic_intent"):
                parts.append(setting["somatic_intent"])
        for card in seg.get("group_cards", []):
            label = card.get("group_label", "")
            card_title = card.get("title", "")
            if label or card_title:
                parts.append(f"{label}: {card_title}".strip(": "))
            if card.get("the_action"):
                parts.append(card["the_action"])
            if card.get("the_body"):
                parts.append(card["the_body"])
    return "\n".join(parts)
