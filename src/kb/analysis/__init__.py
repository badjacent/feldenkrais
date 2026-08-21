"""Analysis engine for Feldenkrais lesson analysis.

Contains pass 0/A/0b analysis functions and framework helpers.
No dependency on FastAPI -- can be imported independently by batch scripts.

Initialize with init(db, data_dir, get_llm_fn) before calling any functions.

This package re-exports all public names so existing imports like
``from analysis import run_pass_a`` continue to work unchanged.
"""

# ── Core: module state, init, framework loading ───────────────────────────
from .core import (
    _state,
    init,
    _resolve_data_path,
    _resolve_prompts_dir,
    _framework_version_suffix,
    load_frameworks,
    get_active_frameworks_config,
)

# ── Framework builders ────────────────────────────────────────────────────
from .framework_builders import (
    _derive_key,
    build_classification_table,
    build_pass_a_schema,
    ROUTER_SYSTEM_PROMPT,
)

# ── Pass 0 ────────────────────────────────────────────────────────────────
from .pass0 import run_pass_0, format_text_with_segments

# ── Pass A ────────────────────────────────────────────────────────────────
from .pass_a import load_pass_a, _pass_a_cache_key, run_pass_a

# ── Pass 0b loaders ──────────────────────────────────────────────────────
from .pass0b_loaders import (
    load_pass_0b,
    load_pass_0b_lang,
    load_pass_0b_word_lang,
    load_pass_0b_condensed,
    load_pass_0b_segmentation,
    load_pass_0b_concepts,
    load_pass_0b_spatial,
)

# ── Pass 0b formatters ───────────────────────────────────────────────────
from .pass0b_formatters import (
    format_steps_for_0b,
    format_steps_for_word_lang,
    format_input_for_segmentation,
    format_input_for_0b_condensed,
    format_input_for_concepts,
    format_input_for_spatial,
)

# ── Pass 0b runners ─────────────────────────────────────────────────────
from .pass0b_somatic import run_pass_0b
from .pass0b_lang import run_pass_0b_lang, run_pass_0b_word_lang
from .pass0b_synthesis import run_segmentation_query, _run_condensed_for_groups, run_pass_0b_condensed
from .pass0b_concepts import run_pass_0b_concepts
from .pass0b_spatial import run_pass_0b_spatial
from .pass0b_spatial_v3 import run_pass_0b_spatial_v3, load_pass_0b_spatial_v3

# ── Re-exported cache helpers (pipeline/analysis.py imports these from analysis) ──
from cache_helpers import get_cache_key, load_cached_analysis, save_cached_analysis, save_prompt_io


# ── Dynamic attribute access for mutable module state ─────────────────────

# Map of attribute names on this package to their keys in _state.
_STATE_KEYS = {
    "_db", "_data_dir", "_get_llm", "PROMPTS_DIR",
    "FRAMEWORK_VERSION", "_frameworks_cache",
}


def __getattr__(name):
    """Provide access to mutable module state that lives in core._state.

    This lets ``analysis.FRAMEWORK_VERSION``, ``analysis._data_dir``, etc.
    resolve to the current value, even after init() updates them.
    """
    if name in _STATE_KEYS:
        return _state[name]
    raise AttributeError(f"module 'analysis' has no attribute {name!r}")


# ── Distinctive motifs ────────────────────────────────────────────────────
from .motifs import load_motifs, load_motif_registry, run_motifs, run_raw_motifs, run_motifs_lite  # noqa: E402
from .axes import load_axes, run_axes, run_axes_lite  # noqa: E402
from .lesson_card import run_lesson_card  # noqa: E402
