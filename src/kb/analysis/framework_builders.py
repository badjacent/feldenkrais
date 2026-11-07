"""Framework schema builders and classification helpers.

Contains _derive_key, build_classification_table, build_pass_a_schema,
ROUTER_SYSTEM_PROMPT, and helper derivation functions.
"""

from .core import _resolve_data_path

import json
import sys

from .core import _state


def _derive_key(char: dict) -> str:
    """Derive Pass A classification key from character id + name.

    VL-1 + The Riser -> vl1_riser
    """
    if "key" in char:
        return char["key"]
    base = char["id"].lower().replace("-", "")
    name_part = char["name"].lower().replace("the ", "").replace(" ", "_")
    return f"{base}_{name_part}"


def build_classification_table(framework_version: str | None = None) -> str:
    """Build the VL classification markdown table from the framework registry."""
    _pkg = sys.modules["analysis"]
    rows = []
    for f in _pkg.get_active_frameworks_config(framework_version):
        focus = f.get("focus") or _derive_focus(f)
        indicators = f.get("indicators") or _derive_indicators(f)
        rows.append(f"| **{f['id']}** {f['name']} | {focus} | {indicators} |")
    header = "| Framework | Focus | Key Indicators |\n|-----------|-------|----------------|\n"
    return header + "\n".join(rows)


def build_pass_a_schema(framework_version: str | None = None) -> dict:
    """Build Pass A schema with dynamic classification properties from registry."""
    _pkg = sys.modules["analysis"]
    schema = json.loads((_resolve_data_path("pass_a.schema.json")).read_text())
    active = _pkg.get_active_frameworks_config(framework_version)
    cls_props = {}
    cls_required = []
    for f in active:
        key = _derive_key(f)
        cls_props[key] = {"type": "number", "minimum": 0, "maximum": 1}
        cls_required.append(key)
    schema["properties"]["classifications"]["properties"] = cls_props
    schema["properties"]["classifications"]["required"] = cls_required
    schema["properties"]["primary_framework"]["enum"] = [f["id"] for f in active]
    schema["properties"]["secondary_frameworks"]["items"] = {
        "type": "string",
        "enum": [f["id"] for f in active],
    }
    return schema


def _derive_focus(char: dict) -> str:
    """Derive focus from core_verb + key phrases from essence.regulates."""
    if "focus" in char:
        return char["focus"]
    essence = char.get("essence", {})
    verb = essence.get("core_verb", "")
    regulates = essence.get("regulates", "")
    reg_sentences = [s.strip() for s in regulates.split(".") if s.strip()]
    reg_part = reg_sentences[0] if reg_sentences else ""
    return f"{verb.title()}: {reg_part}" if verb else reg_part


def _derive_indicators(char: dict) -> str:
    """Derive indicators from first 4-5 vocabulary_seeds."""
    if "indicators" in char:
        return char["indicators"]
    voice = char.get("voice", {})
    seeds = voice.get("vocabulary_seeds", [])
    return ", ".join(seeds[:5])


# ── System prompts ─────────────────────────────────────────────────────────

ROUTER_SYSTEM_PROMPT = """You are an expert Feldenkrais practitioner classifying lessons.
Analyze the lesson text and classify it according to which VL framework(s) best describe its pedagogical focus.
Be precise with your scores and provide specific indicators from the lesson text."""
