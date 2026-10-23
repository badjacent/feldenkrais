"""Lesson group management and analysis gates."""

from typing import Optional

from .lesson_metadata import analyzed_sql

DEFAULT_ANALYSIS_GATE_SOURCE = "FELDENKRAIS_FIRST"


class LessonGroupsMixin:
    """Lesson groups, group membership, and analysis gate controls."""

    def get_lesson_groups(self) -> list[dict]:
        """Return all lesson groups with member counts (all, and analyzed only)."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(f"""
                SELECT lg.*, COUNT(lgm.document_id) as doc_count,
                       COUNT(lgm.document_id) FILTER (
                           WHERE {analyzed_sql("lgm.document_id")}) as analyzed_count
                FROM lesson_group lg
                LEFT JOIN lesson_group_member lgm ON lgm.group_id = lg.id
                GROUP BY lg.id
                ORDER BY lg.name
            """)
            return [dict(row) for row in cursor.fetchall()]

    def get_lesson_group(self, group_id: int) -> Optional[dict]:
        """Return a single lesson group by ID."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute("SELECT * FROM lesson_group WHERE id = %s", (group_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def create_lesson_group(self, name: str, description: str | None = None,
                            auto_source_id: int | None = None) -> int:
        """Create a lesson group. Returns the new group ID."""
        with self.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO lesson_group (name, description, auto_source_id)
                VALUES (%s, %s, %s)
                ON CONFLICT (name) DO UPDATE SET description = EXCLUDED.description
                RETURNING id
            """, (name, description, auto_source_id))
            return cursor.fetchone()["id"]

    def add_group_members(self, group_id: int, document_ids: list[str]):
        """Add documents to a lesson group."""
        with self.get_cursor() as cursor:
            for doc_id in document_ids:
                cursor.execute("""
                    INSERT INTO lesson_group_member (group_id, document_id)
                    VALUES (%s, %s)
                    ON CONFLICT DO NOTHING
                """, (group_id, doc_id))

    def get_group_members(self, group_id: int) -> list[dict]:
        """Return documents in a lesson group, flagged analyzed or transcript-only."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(f"""
                SELECT d.id as document_id, d.title, d.created_at,
                       ds.name as source_name, {analyzed_sql()} as analyzed
                FROM lesson_group_member lgm
                JOIN document d ON d.id = lgm.document_id
                JOIN document_source ds ON ds.id = d.source_id
                WHERE lgm.group_id = %s
                ORDER BY d.title
            """, (group_id,))
            return [dict(row) for row in cursor.fetchall()]

    def sync_auto_groups(self):
        """Sync auto-source groups: create groups for sources with documents, populate members."""
        with self.get_cursor() as cursor:
            # Find sources that have transcribed documents
            cursor.execute("""
                SELECT DISTINCT ds.id, ds.name
                FROM document_source ds
                JOIN document d ON d.source_id = ds.id
                JOIN analysis a ON a.document_id = d.id AND a.type_id = 1
            """)
            sources = cursor.fetchall()

            for src in sources:
                # Create or get group
                cursor.execute("""
                    INSERT INTO lesson_group (name, auto_source_id)
                    VALUES (%s, %s)
                    ON CONFLICT (name) DO UPDATE SET auto_source_id = EXCLUDED.auto_source_id
                    RETURNING id, (xmax = 0) AS inserted
                """, (src["name"], src["id"]))
                group_row = cursor.fetchone()
                group_id = group_row["id"]

                # A new source inherits the curated processing surface from
                # FELDENKRAIS_FIRST. Do not implicitly enable every registered
                # analysis step: new steps remain owner-gated there first.
                if group_row.get("inserted") and src["name"] != DEFAULT_ANALYSIS_GATE_SOURCE:
                    cursor.execute("""
                        INSERT INTO group_analysis_gate
                            (group_id, profile_key, step_name, permitted, updated_at)
                        SELECT %s, template.profile_key, template.step_name, template.permitted, NOW()
                        FROM lesson_group template_group
                        JOIN document_source template_source
                          ON template_source.id = template_group.auto_source_id
                        JOIN group_analysis_gate template
                          ON template.group_id = template_group.id
                        JOIN analysis_step step
                          ON step.name = template.step_name AND NOT step.retired
                        WHERE template_source.name = %s
                        ON CONFLICT (group_id, profile_key, step_name) DO NOTHING
                    """, (group_id, DEFAULT_ANALYSIS_GATE_SOURCE))

                # Sync members: add documents from this source that have transcriptions
                cursor.execute("""
                    INSERT INTO lesson_group_member (group_id, document_id)
                    SELECT %s, d.id
                    FROM document d
                    JOIN analysis a ON a.document_id = d.id AND a.type_id = 1
                    WHERE d.source_id = %s
                    ON CONFLICT DO NOTHING
                """, (group_id, src["id"]))

    # ── Group analysis gates ──────────────────────────────────────

    def get_group_gates(self, group_id: int, profile_key: str | None = None) -> list[dict]:
        """Return gate entries for a group, for one profile or all of them."""
        profile_filter = "AND gag.profile_key = %s" if profile_key else ""
        params = (group_id, profile_key) if profile_key else (group_id,)
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(f"""
                SELECT gag.*, s.current_version, s.depends_on, s.description
                FROM group_analysis_gate gag
                JOIN analysis_step s ON s.name = gag.step_name AND NOT s.retired
                WHERE gag.group_id = %s {profile_filter}
                ORDER BY gag.profile_key, s.name
            """, params)
            return [dict(row) for row in cursor.fetchall()]

    def set_group_gate(self, group_id: int, step_name: str, permitted: bool, *,
                       profile_key: str):
        """Set the permitted flag for a (group, profile, step) triple.

        A retired step can be closed but never opened.
        """
        if permitted:
            step = self.get_analysis_step(step_name)
            if step and step.get("retired"):
                raise ValueError(f"Step {step_name} is retired and cannot be permitted")
        with self.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO group_analysis_gate (group_id, profile_key, step_name, permitted, updated_at)
                VALUES (%s, %s, %s, %s, NOW())
                ON CONFLICT (group_id, profile_key, step_name)
                DO UPDATE SET permitted = EXCLUDED.permitted, updated_at = NOW()
            """, (group_id, profile_key, step_name, permitted))
