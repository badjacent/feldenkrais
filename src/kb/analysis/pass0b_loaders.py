"""Loaders for Pass 0b prompts and schemas.

Each loader returns a (prompt_str, schema_dict) tuple.
"""

import json

from .core import _state


def load_pass_0b() -> tuple[str, dict]:
    """Load Pass 0b prompt and schema (Step-Level Enrichment)."""
    prompt_path = _state["_data_dir"] / "pass_0b.md"
    schema_path = _state["_data_dir"] / "pass_0b.schema.json"
    prompt = prompt_path.read_text() if prompt_path.exists() else ""
    schema = json.loads(schema_path.read_text()) if schema_path.exists() else {}
    return prompt, schema


def load_pass_0b_lang() -> tuple[str, dict]:
    """Load Pass 0b-lang prompt and schema (Step-Level Language Analysis)."""
    prompt_path = _state["_data_dir"] / "pass_0b_lang.md"
    schema_path = _state["_data_dir"] / "pass_0b_lang.schema.json"
    prompt = prompt_path.read_text() if prompt_path.exists() else ""
    schema = json.loads(schema_path.read_text()) if schema_path.exists() else {}
    return prompt, schema


def load_pass_0b_word_lang() -> tuple[str, dict]:
    """Load Pass 0b word-level language annotation prompt and schema."""
    prompt_path = _state["_data_dir"] / "pass_0b_word_lang.md"
    schema_path = _state["_data_dir"] / "pass_0b_word_lang.schema.json"
    prompt = prompt_path.read_text() if prompt_path.exists() else ""
    schema = json.loads(schema_path.read_text()) if schema_path.exists() else {}
    return prompt, schema


def load_pass_0b_condensed() -> tuple[str, dict]:
    """Load Pass 0b condensed prompt and schema."""
    prompt_path = _state["_data_dir"] / "pass_0b_condensed.md"
    schema_path = _state["_data_dir"] / "pass_0b_condensed.schema.json"
    prompt = prompt_path.read_text() if prompt_path.exists() else ""
    schema = json.loads(schema_path.read_text()) if schema_path.exists() else {}
    return prompt, schema


def load_pass_0b_segmentation() -> tuple[str, dict]:
    """Load Pass 0b segmentation prompt and schema."""
    prompt_path = _state["_data_dir"] / "pass_0b_segmentation.md"
    schema_path = _state["_data_dir"] / "pass_0b_segmentation.schema.json"
    prompt = prompt_path.read_text() if prompt_path.exists() else ""
    schema = json.loads(schema_path.read_text()) if schema_path.exists() else {}
    return prompt, schema


def load_pass_0b_concepts() -> tuple[str, dict]:
    """Load Pass 0b concepts prompt and schema (lesson-level concept tagging)."""
    prompt_path = _state["_data_dir"] / "pass_0b_concepts.md"
    schema_path = _state["_data_dir"] / "pass_0b_concepts.schema.json"
    prompt = prompt_path.read_text() if prompt_path.exists() else ""
    schema = json.loads(schema_path.read_text()) if schema_path.exists() else {}
    return prompt, schema


def load_pass_0b_spatial() -> tuple[str, dict]:
    """Load Pass 0b spatial prompt and schema (per-group spatial configs)."""
    prompt_path = _state["_data_dir"] / "pass_0b_spatial.md"
    schema_path = _state["_data_dir"] / "pass_0b_spatial.schema.json"
    prompt = prompt_path.read_text() if prompt_path.exists() else ""
    schema = json.loads(schema_path.read_text()) if schema_path.exists() else {}
    return prompt, schema
