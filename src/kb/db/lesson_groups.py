"""Lesson group management and analysis gates."""

from typing import Optional


class LessonGroupsMixin:
    """Lesson groups, group membership, and analysis gate controls."""

    def get_lesson_groups(self) -> list[dict]:
        """Return all lesson groups with member counts."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute("""
                SELECT lg.*, COUNT(lgm.document_id) as doc_count
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
        """Return documents in a lesson group."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute("""
                SELECT d.id as document_id, d.title, d.created_at,
                       ds.name as source_name
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
                    RETURNING id
                """, (src["name"], src["id"]))
                group_id = cursor.fetchone()["id"]

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

    def get_group_gates(self, group_id: int) -> list[dict]:
        """Return all gate entries for a group."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute("""
                SELECT gag.*, s.current_version, s.depends_on, s.description
                FROM group_analysis_gate gag
                JOIN analysis_step s ON s.name = gag.step_name
                WHERE gag.group_id = %s
                ORDER BY s.name
            """, (group_id,))
            return [dict(row) for row in cursor.fetchall()]

    def set_group_gate(self, group_id: int, step_name: str, permitted: bool):
        """Set the permitted flag for a (group, step) pair."""
        with self.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO group_analysis_gate (group_id, step_name, permitted, updated_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (group_id, step_name)
                DO UPDATE SET permitted = EXCLUDED.permitted, updated_at = NOW()
            """, (group_id, step_name, permitted))
