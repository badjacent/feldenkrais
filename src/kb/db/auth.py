"""User authentication, access control, and group management."""

import json


class AuthMixin:
    """User management, source access control, and access groups."""

    def upsert_user(self, provider: str, provider_id: str, login: str,
                    name: str = None, email: str = None,
                    avatar_url: str = None) -> dict:
        """Insert or update an OAuth user. Returns the user dict.

        Matches on (provider, login) to handle provider_id format changes
        (e.g. Google switching from email to numeric ID). Updates provider_id
        on match so the record stays current.
        """
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO users (provider, provider_id, login, name, email, avatar_url)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (provider, login) DO UPDATE SET
                    provider_id = EXCLUDED.provider_id,
                    name = EXCLUDED.name,
                    email = EXCLUDED.email,
                    avatar_url = EXCLUDED.avatar_url,
                    last_login_at = NOW()
                RETURNING id, provider, provider_id, login, name, email,
                          avatar_url, tier, superuser_eligible, created_at, last_login_at
                """,
                (provider, provider_id, login, name, email, avatar_url),
            )
            return dict(cursor.fetchone())

    def get_user(self, user_id: int) -> dict | None:
        """Get a user by id. Returns dict or None."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def is_login_allowed(self, login: str) -> bool:
        """Check if a GitHub login is on the allowlist."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute("SELECT 1 FROM allowed_logins WHERE login = %s", (login,))
            return cursor.fetchone() is not None

    def get_user_source_ids(self, user_id: int) -> list[int]:
        """Return source_ids this user has been granted access to."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(
                "SELECT source_id FROM user_source_access WHERE user_id = %s",
                (user_id,),
            )
            return [row["source_id"] for row in cursor.fetchall()]

    def get_user_beta_features(self, user_id: int) -> list[str]:
        """Return beta feature names this user has access to."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(
                "SELECT feature FROM user_beta_access WHERE user_id = %s",
                (user_id,),
            )
            return [row["feature"] for row in cursor.fetchall()]

    # ── Group-based source access ──────────────────────────────────────

    def get_user_allowed_sources(self, user_id: int | None) -> set[int]:
        """Return source IDs visible to a user: public sources UNION group-granted sources.

        If user_id is None (anonymous), returns only public source IDs.
        """
        with self.get_cursor(commit=False) as cursor:
            # Public sources are visible to everyone
            cursor.execute("SELECT id FROM document_source WHERE public = TRUE AND visible = TRUE")
            ids = {row["id"] for row in cursor.fetchall()}
            if user_id is not None:
                # Add sources granted via group membership
                cursor.execute("""
                    SELECT DISTINCT gsa.source_id
                    FROM group_source_access gsa
                    JOIN user_group ug ON ug.group_id = gsa.group_id
                    WHERE ug.user_id = %s
                """, (user_id,))
                ids |= {row["source_id"] for row in cursor.fetchall()}
            return ids

    def _source_filter(self) -> tuple[str, str, list]:
        """Build SQL fragments for source-based access filtering.

        Returns (join_clause, where_clause, params).
        - If allowed_source_ids is None: no filtering (superuser/admin).
        - If allowed_source_ids is a set: filter to those IDs.
        """
        if self.allowed_source_ids is None:
            return "", "", []
        ids = list(self.allowed_source_ids)
        return (
            "\nJOIN document_source ds ON ds.id = d.source_id",
            " AND ds.id = ANY(%s)",
            [ids],
        )

    def is_document_accessible(self, doc_id: str) -> bool:
        """Check if a document belongs to an allowed source. Always True if no filtering."""
        if self.allowed_source_ids is None:
            return True
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(
                "SELECT source_id FROM document WHERE id = %s", (doc_id,),
            )
            row = cursor.fetchone()
            if not row:
                return False
            return row["source_id"] in self.allowed_source_ids

    def create_access_group(self, name: str) -> dict:
        """Create an access group. Returns the new group dict."""
        with self.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO access_group (name) VALUES (%s) RETURNING id, name, created_at",
                (name,),
            )
            return dict(cursor.fetchone())

    def add_user_to_group(self, user_id: int, group_id: int):
        """Add a user to an access group."""
        with self.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO user_group (user_id, group_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (user_id, group_id),
            )

    def add_source_to_group(self, source_id: int, group_id: int):
        """Grant a group access to a document source."""
        with self.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO group_source_access (group_id, source_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (group_id, source_id),
            )

    def log_search_query(self, query: str, result_count: int = None,
                         specificity_band: str = None, specificity_gap: float = None,
                         top_results: list = None, user_id: int = None):
        """Log a search query for usage analysis."""
        top_json = json.dumps(top_results) if top_results else None
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO search_query_log
                    (query, result_count, specificity_band, specificity_gap, top_results, user_id)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s)
                """,
                (query, result_count, specificity_band, specificity_gap, top_json, user_id),
            )

    def update_source_category_name(self, source_id: int, category_name: str):
        """Set the display category name for a document source."""
        with self.get_cursor() as cursor:
            cursor.execute(
                "UPDATE document_source SET category_name = %s WHERE id = %s",
                (category_name, source_id),
            )
