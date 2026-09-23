"""Analysis profiles: one row per model configuration that analysis runs under.

A profile names a provider, model and parameters, the schema contract its
results are produced under, and the cache tag that keeps its results apart
from every other profile's. Analysis progress, gates and derived rows are all
keyed by profile, so one document can carry one analysis per model.

A profile never changes once it has produced results. A different effort
level or model is a new profile.
"""

from typing import Optional

# The profile that historical results were produced under. Its cache tag is
# NULL, so its results live under the original, unsuffixed cache keys.
LEGACY_PROFILE_KEY = "gemini-3-flash"

# SQL expression for the canonical profile's key, for readers that default to it.
CANONICAL_PROFILE_SQL = "(SELECT key FROM analysis_profile WHERE canonical)"

# Analysis modules append ``":" + provider_key`` to cache keys unless the key
# is this value (llm_config.DEFAULT_LLM). Kept here to avoid an import cycle.
_BARE_KEY_PROVIDER = "gemini"


def profile_provider_key(profile: dict) -> str:
    """Return the key analysis modules receive as ``provider_key``.

    The analysis package builds its cache suffix from this key and resolves its
    LLM client from it, so it must be unique per profile:

    - a NULL cache tag gives the bare-key provider (historical results);
    - contract 2 gives the cache tag itself (``codex-astra``);
    - contract 3+ prefixes the contract (``c3:astra-medium``), so results made
      under a different schema contract can never share a cache entry.
    """
    tag = profile.get("cache_tag")
    if not tag:
        return _BARE_KEY_PROVIDER
    contract = int(profile.get("contract_version") or 2)
    return f"c{contract}:{tag}" if contract >= 3 else tag


def profile_cache_suffix(profile: dict) -> str:
    """Return the suffix appended after a step's own cache suffix."""
    key = profile_provider_key(profile)
    return "" if key == _BARE_KEY_PROVIDER else f":{key}"


class AnalysisProfilesMixin:
    """Read and switch analysis profiles."""

    def get_analysis_profiles(self) -> list[dict]:
        """Return all profiles, canonical first, then by key."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(
                "SELECT * FROM analysis_profile ORDER BY canonical DESC, key"
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_analysis_profile(self, key: str) -> Optional[dict]:
        """Return one profile by key."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute("SELECT * FROM analysis_profile WHERE key = %s", (key,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_canonical_profile(self) -> dict:
        """Return the profile that pages and search read by default."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute("SELECT * FROM analysis_profile WHERE canonical")
            row = cursor.fetchone()
        if not row:
            raise RuntimeError("No canonical analysis profile is configured")
        return dict(row)

    def resolve_analysis_profile(self, key: str | None) -> dict:
        """Return the named profile, or the canonical one when no key is given."""
        if not key:
            return self.get_canonical_profile()
        profile = self.get_analysis_profile(key)
        if not profile:
            raise ValueError(f"Unknown analysis profile: {key}")
        return profile

    def get_profile_by_provider_key(self, provider_key: str) -> Optional[dict]:
        """Return the profile whose provider key matches, if any."""
        for profile in self.get_analysis_profiles():
            if profile_provider_key(profile) == provider_key:
                return profile
        return None

    def set_profile_enabled(self, key: str, enabled: bool) -> bool:
        """Turn a profile's global switch on or off. Returns False if unknown."""
        with self.get_cursor() as cursor:
            cursor.execute(
                "UPDATE analysis_profile SET enabled = %s, updated_at = NOW() WHERE key = %s",
                (enabled, key),
            )
            return cursor.rowcount == 1
