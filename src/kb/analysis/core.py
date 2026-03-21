"""Core module state and framework loading for the analysis engine.

Contains init(), data path resolution, framework loading/caching,
and shared module state used by all analysis submodules.

All mutable state lives in the ``_state`` dict so it can be shared
across the package and its submodules without import-time binding issues.
The _state dict also checks the package module's __dict__ for overrides,
which supports test fixtures that set e.g. ``analysis._data_dir = X``.
"""

import json
import sys
from pathlib import Path

from cache_helpers import get_cache_key, load_cached_analysis, save_cached_analysis
from llm_config import DEFAULT_LLM


# ── Shared mutable state (initialized via init()) ─────────────────────────

class _StateProxy(dict):
    """Dict-like state container that also checks the package module's __dict__
    for overrides. This supports test fixtures that set attributes directly
    on the analysis package module (e.g. ``analysis._data_dir = some_path``).
    """

    def __getitem__(self, key):
        # Check if the package module has this attribute set directly
        # (e.g. by test fixtures)
        pkg = sys.modules.get("analysis")
        if pkg is not None and key in pkg.__dict__:
            return pkg.__dict__[key]
        return super().__getitem__(key)


_state = _StateProxy({
    "_db": None,
    "_data_dir": None,
    "_get_llm": None,
    "PROMPTS_DIR": None,
    "FRAMEWORK_VERSION": None,
    "_frameworks_cache": {},
})


def init(db, data_dir: Path, get_llm_fn):
    """Initialize the analysis module.

    Args:
        db: Database instance (or None).
        data_dir: Path to the data directory.
        get_llm_fn: Callable that returns an LLM instance (e.g., llm_config.get_llm).
    """
    _state["_db"] = db
    _state["_data_dir"] = data_dir
    _state["_get_llm"] = get_llm_fn
    _state["PROMPTS_DIR"], _state["FRAMEWORK_VERSION"] = _resolve_prompts_dir()


# ── Framework helpers ──────────────────────────────────────────────────────


def _resolve_data_path(relative_path: str) -> Path:
    """Resolve a data file path, checking private/data/ submodule first, then public data/.

    This supports the public/private repo split where some data files live in
    the private submodule at <project_root>/private/data/.
    """
    data_dir = _state["_data_dir"]
    # Check private submodule data dir first
    private_data = data_dir.parent / "private" / "data"
    candidate = private_data / relative_path
    if candidate.exists():
        return candidate
    return data_dir / relative_path


def _resolve_prompts_dir(version: str | None = None) -> tuple[Path, str]:
    """Resolve the versioned prompts directory. If version is None, read from pointer."""
    if version is None:
        pointer = _resolve_data_path("frameworks.json")
        if pointer.exists():
            version = json.loads(pointer.read_text())["current_version"]
        else:
            version = "v1"
    prompts = _resolve_data_path(f"frameworks/{version}")
    return prompts, version


def _framework_version_suffix(framework_version: str | None = None) -> str:
    """Return cache key suffix for framework version. Empty for v1 (backwards compat)."""
    v = framework_version or _state["FRAMEWORK_VERSION"]
    if v == "v1":
        return ""
    return f":f{v}"


def load_frameworks(framework_version: str | None = None) -> list[dict]:
    """Load framework registry for a given version (cached per version)."""
    # Late-bind _resolve_prompts_dir through the package so test patches work
    _pkg = sys.modules.get("analysis")
    _resolve = _pkg._resolve_prompts_dir if _pkg else _resolve_prompts_dir

    frameworks_cache = _state["_frameworks_cache"]
    v = framework_version or _state["FRAMEWORK_VERSION"]
    if v not in frameworks_cache:
        prompts_dir, _ = _resolve(v)
        path = prompts_dir / "frameworks.json"
        registry = json.loads(path.read_text())
        # New flat character-definitions schema
        if "characters" in registry:
            frameworks_cache[v] = registry["characters"]
        else:
            # Legacy nested schema
            ver_key = registry.get("current_version", list(registry.get("versions", {}).keys())[0])
            frameworks_cache[v] = registry["versions"][ver_key]["frameworks"]
    return frameworks_cache[v]


def get_active_frameworks_config(framework_version: str | None = None) -> list[dict]:
    """Return only active (primary) frameworks from a given version."""
    # Late-bind load_frameworks through the package so test patches work
    _pkg = sys.modules.get("analysis")
    _load = _pkg.load_frameworks if _pkg else load_frameworks

    frameworks = _load(framework_version)
    # New schema uses role="primary"; legacy uses active=True
    if frameworks and "role" in frameworks[0]:
        return [f for f in frameworks if f.get("role") == "primary"]
    return [f for f in frameworks if f.get("active", True)]
