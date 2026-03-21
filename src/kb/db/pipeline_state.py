"""Analysis pipeline state: steps, document state, runnable queries."""

from typing import Optional


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

    def get_document_state(self, document_id: str, step_name: str) -> Optional[dict]:
        """Return a single document analysis state entry."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(
                "SELECT * FROM document_analysis_state WHERE document_id = %s AND step_name = %s",
                (document_id, step_name),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def mark_step_completed(self, document_id: str, step_name: str, version: int,
                            upstream_hash: str | None = None):
        """Record that a step completed successfully for a document."""
        with self.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO document_analysis_state
                    (document_id, step_name, completed_version, completed_at, upstream_hash, stale, error, updated_at)
                VALUES (%s, %s, %s, NOW(), %s, FALSE, NULL, NOW())
                ON CONFLICT (document_id, step_name)
                DO UPDATE SET completed_version = EXCLUDED.completed_version,
                             completed_at = NOW(), upstream_hash = EXCLUDED.upstream_hash,
                             stale = FALSE, error = NULL, updated_at = NOW()
            """, (document_id, step_name, version, upstream_hash))
            # Mark downstream steps as stale
            cursor.execute("""
                UPDATE document_analysis_state das
                SET stale = TRUE, updated_at = NOW()
                FROM analysis_step s
                WHERE das.step_name = s.name
                  AND das.document_id = %s
                  AND %s = ANY(s.depends_on)
            """, (document_id, step_name))

    def mark_step_error(self, document_id: str, step_name: str, error: str):
        """Record that a step failed for a document."""
        with self.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO document_analysis_state (document_id, step_name, error, updated_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (document_id, step_name)
                DO UPDATE SET error = EXCLUDED.error, updated_at = NOW()
            """, (document_id, step_name, error))

    def clear_step_error(self, document_id: str, step_name: str):
        """Clear the error on a document analysis state entry."""
        with self.get_cursor() as cursor:
            cursor.execute("""
                UPDATE document_analysis_state
                SET error = NULL, updated_at = NOW()
                WHERE document_id = %s AND step_name = %s
            """, (document_id, step_name))

    def get_runnable_steps(self, document_id: str | None = None) -> list[dict]:
        """Find all (document, step) pairs that are permitted via group gates, need work, and have deps satisfied.

        A step is runnable when:
        1. At least one group containing the document has group_analysis_gate.permitted = TRUE
        2. document_analysis_state has completed_version IS NULL or < current_version or stale = TRUE
        3. error IS NULL
        4. All depends_on steps have completed_version IS NOT NULL for this document
        """
        doc_filter = "AND d.id = %s" if document_id else ""
        params = [document_id] if document_id else []

        with self.get_cursor(commit=False) as cursor:
            cursor.execute(f"""
                WITH candidates AS (
                    SELECT DISTINCT d.id as document_id, d.title as document_title,
                           s.name as step_name, s.current_version, s.depends_on, s.cache_suffix,
                           das.completed_version, das.stale
                    FROM lesson_group_member lgm
                    JOIN group_analysis_gate gag ON gag.group_id = lgm.group_id AND gag.permitted = TRUE
                    JOIN analysis_step s ON s.name = gag.step_name
                    JOIN document d ON d.id = lgm.document_id
                    LEFT JOIN document_analysis_state das ON das.document_id = d.id AND das.step_name = s.name
                    WHERE (das.completed_version IS NULL
                           OR das.completed_version < s.current_version
                           OR das.stale = TRUE)
                      AND (das.error IS NULL)
                      {doc_filter}
                )
                SELECT c.*
                FROM candidates c
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM unnest(c.depends_on) dep(name)
                    WHERE NOT EXISTS (
                        SELECT 1 FROM document_analysis_state das2
                        WHERE das2.document_id = c.document_id
                          AND das2.step_name = dep.name
                          AND das2.completed_version IS NOT NULL
                    )
                )
                ORDER BY c.document_title, c.step_name
            """, params)
            return [dict(row) for row in cursor.fetchall()]

    def count_runnable_steps(self) -> int:
        """Count of runnable steps including dep check."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute("""
                WITH candidates AS (
                    SELECT DISTINCT lgm.document_id, s.name as step_name, s.depends_on
                    FROM lesson_group_member lgm
                    JOIN group_analysis_gate gag ON gag.group_id = lgm.group_id AND gag.permitted = TRUE
                    JOIN analysis_step s ON s.name = gag.step_name
                    LEFT JOIN document_analysis_state das ON das.document_id = lgm.document_id AND das.step_name = s.name
                    WHERE (das.completed_version IS NULL
                           OR das.completed_version < s.current_version
                           OR das.stale = TRUE)
                      AND (das.error IS NULL)
                )
                SELECT COUNT(*) as count FROM candidates c
                WHERE NOT EXISTS (
                    SELECT 1 FROM unnest(c.depends_on) dep(name)
                    WHERE NOT EXISTS (
                        SELECT 1 FROM document_analysis_state das2
                        WHERE das2.document_id = c.document_id
                          AND das2.step_name = dep.name
                          AND das2.completed_version IS NOT NULL
                    )
                )
            """)
            return cursor.fetchone()["count"]
