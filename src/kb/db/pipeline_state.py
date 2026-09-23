"""Analysis pipeline state: steps, document state, runnable queries."""

from typing import Optional

from .analysis_profiles import CANONICAL_PROFILE_SQL


class PipelineStateMixin:
    """Analysis step definitions and per-document completion tracking."""

    def ensure_pipeline_node_state_table(self):
        """Create pipeline_node_state table if it doesn't exist."""
        with self.get_cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS pipeline_node_state (
                    name TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'pending',
                    input_hash TEXT NOT NULL DEFAULT '',
                    output_hash TEXT NOT NULL DEFAULT '',
                    last_run_at TIMESTAMPTZ,
                    duration_seconds REAL,
                    error_message TEXT,
                    metadata JSONB,
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

    def get_analysis_steps(self) -> list[dict]:
        """Return all registered analysis steps."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute("SELECT * FROM analysis_step ORDER BY name")
            return [dict(row) for row in cursor.fetchall()]

    def get_analysis_step(self, name: str) -> Optional[dict]:
        """Return a single analysis step by name."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute("SELECT * FROM analysis_step WHERE name = %s", (name,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def update_analysis_step_version(self, name: str, new_version: int):
        """Bump an analysis step to a new version."""
        with self.get_cursor() as cursor:
            cursor.execute(
                "UPDATE analysis_step SET current_version = %s, updated_at = NOW() WHERE name = %s",
                (new_version, name),
            )

    # ── Document analysis state ───────────────────────────────────
    #
    # Every row is keyed by (document, profile, step): one document carries one
    # analysis per model. Writers name their profile explicitly; readers fall
    # back to the canonical profile when none is given.

    def get_document_state(self, document_id: str, step_name: str,
                           profile_key: str | None = None) -> Optional[dict]:
        """Return a single document analysis state entry for one profile."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(
                f"""
                SELECT * FROM document_analysis_state
                WHERE document_id = %s AND step_name = %s
                  AND profile_key = COALESCE(%s, {CANONICAL_PROFILE_SQL})
                """,
                (document_id, step_name, profile_key),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_document_states(self, document_id: str) -> list[dict]:
        """Return every state row for a document, across all profiles."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(
                "SELECT * FROM document_analysis_state WHERE document_id = %s "
                "ORDER BY profile_key, step_name",
                (document_id,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def mark_step_completed(self, document_id: str, step_name: str, version: int,
                            upstream_hash: str | None = None, *,
                            profile_key: str, contract_version: int):
        """Record that a step completed successfully for a document under a profile."""
        with self.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO document_analysis_state
                    (document_id, profile_key, step_name, completed_version, contract_version,
                     completed_at, upstream_hash, stale, error, updated_at)
                VALUES (%s, %s, %s, %s, %s, NOW(), %s, FALSE, NULL, NOW())
                ON CONFLICT (document_id, profile_key, step_name)
                DO UPDATE SET completed_version = EXCLUDED.completed_version,
                             contract_version = EXCLUDED.contract_version,
                             completed_at = NOW(), upstream_hash = EXCLUDED.upstream_hash,
                             stale = FALSE, error = NULL, updated_at = NOW()
            """, (document_id, profile_key, step_name, version, contract_version, upstream_hash))
            # Mark this profile's downstream steps as stale
            cursor.execute("""
                UPDATE document_analysis_state das
                SET stale = TRUE, updated_at = NOW()
                FROM analysis_step s
                WHERE das.step_name = s.name
                  AND das.document_id = %s
                  AND das.profile_key = %s
                  AND %s = ANY(s.depends_on)
            """, (document_id, profile_key, step_name))

    def mark_step_error(self, document_id: str, step_name: str, error: str, *,
                        profile_key: str):
        """Record that a step failed for a document under a profile."""
        with self.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO document_analysis_state (document_id, profile_key, step_name, error, updated_at)
                VALUES (%s, %s, %s, %s, NOW())
                ON CONFLICT (document_id, profile_key, step_name)
                DO UPDATE SET error = EXCLUDED.error, updated_at = NOW()
            """, (document_id, profile_key, step_name, error))

    def clear_step_error(self, document_id: str, step_name: str, *, profile_key: str):
        """Clear the error on a document analysis state entry."""
        with self.get_cursor() as cursor:
            cursor.execute("""
                UPDATE document_analysis_state
                SET error = NULL, updated_at = NOW()
                WHERE document_id = %s AND step_name = %s AND profile_key = %s
            """, (document_id, step_name, profile_key))

    def invalidate_step(self, document_id: str, step_name: str, *, profile_key: str):
        """Mark a step and all its downstream dependents stale for one profile.

        Clears errors, sets stale=TRUE, and deletes that profile's cache entries
        so the LLM is called fresh. Other profiles' results are untouched.
        """
        import hashlib
        from .analysis_profiles import profile_cache_suffix

        profile = self.get_analysis_profile(profile_key)
        if not profile:
            raise ValueError(f"Unknown analysis profile: {profile_key}")
        tag = profile_cache_suffix(profile)

        with self.get_cursor() as cursor:
            # Collect all step names to invalidate (target + downstream)
            cursor.execute("""
                WITH RECURSIVE downstream AS (
                    SELECT %s::text AS name
                    UNION
                    SELECT s.name
                    FROM analysis_step s
                    JOIN downstream d ON d.name = ANY(s.depends_on)
                )
                SELECT d.name, s.cache_suffix
                FROM downstream d
                JOIN analysis_step s ON s.name = d.name
            """, (step_name,))
            steps_to_invalidate = cursor.fetchall()

            for row in steps_to_invalidate:
                cursor.execute("""
                    INSERT INTO document_analysis_state
                        (document_id, profile_key, step_name, stale, error, updated_at)
                    VALUES (%s, %s, %s, TRUE, NULL, NOW())
                    ON CONFLICT (document_id, profile_key, step_name)
                    DO UPDATE SET stale = TRUE, error = NULL, updated_at = NOW()
                """, (document_id, profile_key, row["name"]))

            # Clear this profile's cache entries so the LLM runs fresh.
            # Cache keys are sha256(transcript + step suffix + profile tag)[:16].
            cursor.execute("""
                SELECT a.data->>'text' AS text
                FROM analysis a WHERE a.document_id = %s AND a.type_id = 1
            """, (document_id,))
            tx_row = cursor.fetchone()
            if tx_row and tx_row["text"]:
                transcript = tx_row["text"].strip()
                for row in steps_to_invalidate:
                    suffix = row.get("cache_suffix")
                    if suffix:
                        cache_key = hashlib.sha256(
                            (transcript + suffix + tag).encode()
                        ).hexdigest()[:16]
                        cursor.execute(
                            "DELETE FROM cache WHERE key = %s OR key = %s",
                            (cache_key, cache_key + ":prompt"),
                        )

    # A (document, profile, step) needs work when a permitted gate reaches it
    # and it has never completed, completed under an older step version or a
    # different schema contract than its profile runs now, or is stale.
    # Dependencies must be complete for the same profile.
    _RUNNABLE_CTE = """
        WITH candidates AS (
            SELECT DISTINCT d.id as document_id, d.title as document_title,
                   p.key as profile_key, p.contract_version as profile_contract,
                   s.name as step_name, s.current_version, s.depends_on, s.cache_suffix,
                   das.completed_version, das.stale
            FROM lesson_group_member lgm
            JOIN group_analysis_gate gag ON gag.group_id = lgm.group_id AND gag.permitted = TRUE
            JOIN analysis_profile p ON p.key = gag.profile_key {profile_filter}
            JOIN analysis_step s ON s.name = gag.step_name
            JOIN document d ON d.id = lgm.document_id
            LEFT JOIN document_analysis_state das
              ON das.document_id = d.id AND das.step_name = s.name AND das.profile_key = p.key
            WHERE (das.completed_version IS NULL
                   OR das.completed_version < s.current_version
                   OR das.contract_version IS DISTINCT FROM p.contract_version
                   OR das.stale = TRUE)
              AND (das.error IS NULL)
              {doc_filter}
        ),
        runnable AS (
            SELECT c.*
            FROM candidates c
            WHERE NOT EXISTS (
                SELECT 1
                FROM unnest(c.depends_on) dep(name)
                WHERE NOT EXISTS (
                    SELECT 1 FROM document_analysis_state das2
                    WHERE das2.document_id = c.document_id
                      AND das2.profile_key = c.profile_key
                      AND das2.step_name = dep.name
                      AND das2.completed_version IS NOT NULL
                      AND das2.stale IS NOT TRUE
                      AND das2.error IS NULL
                )
            )
        )
    """

    def _runnable_filters(self, document_id, profile_key, enabled_only):
        profile_filter = ""
        params: list = []
        if enabled_only:
            profile_filter += " AND p.enabled"
        if profile_key:
            profile_filter += " AND p.key = %s"
            params.append(profile_key)
        doc_filter = ""
        if document_id:
            doc_filter = "AND d.id = %s"
            params.append(document_id)
        return self._RUNNABLE_CTE.format(profile_filter=profile_filter, doc_filter=doc_filter), params

    def get_runnable_steps(self, document_id: str | None = None, *,
                           profile_key: str | None = None,
                           enabled_only: bool = True) -> list[dict]:
        """Find (document, profile, step) triples that are permitted, need work, and have deps satisfied.

        By default only profiles whose global switch is on are considered, which
        is what the worker claims from.
        """
        cte, params = self._runnable_filters(document_id, profile_key, enabled_only)
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(
                cte + "SELECT * FROM runnable ORDER BY document_title, profile_key, step_name",
                params,
            )
            return [dict(row) for row in cursor.fetchall()]

    def count_runnable_steps(self, *, profile_key: str | None = None,
                             enabled_only: bool = True) -> int:
        """Count of runnable steps including the dependency check."""
        cte, params = self._runnable_filters(None, profile_key, enabled_only)
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(cte + "SELECT COUNT(*) as count FROM runnable", params)
            return cursor.fetchone()["count"]

    def count_runnable_steps_by_profile(self) -> dict[str, int]:
        """Runnable work per profile, whether or not the profile is switched on.

        For a switched-off profile this is the work that would start if it were
        turned on.
        """
        cte, params = self._runnable_filters(None, None, False)
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(
                cte + "SELECT profile_key, COUNT(*) as count FROM runnable GROUP BY profile_key",
                params,
            )
            return {row["profile_key"]: row["count"] for row in cursor.fetchall()}
