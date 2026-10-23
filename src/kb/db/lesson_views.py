"""Lesson view tracking and Q&A query logging."""


class LessonViewsMixin:
    """Track lesson page views and chat queries per user."""

    def log_lesson_view(self, document_id: str, user_id: int | None):
        """Record that a user viewed a lesson page."""
        with self.get_cursor() as cur:
            cur.execute(
                """INSERT INTO lesson_view (document_id, user_id)
                   VALUES (%s, %s)""",
                (document_id, user_id),
            )

    def log_lesson_chat(self, document_id: str, user_id: int | None,
                        query: str, response: str):
        """Append a Q&A exchange to the lesson's chat log.

        Resilient to stale pool connections: Neon idle-closes sockets that the
        psycopg2 pool then hands out as zombies. On the first attempt's
        OperationalError/InterfaceError, drop the pool and retry once with a
        fresh connection.
        """
        import psycopg2
        for attempt in (1, 2):
            try:
                with self.get_cursor() as cur:
                    cur.execute(
                        """INSERT INTO lesson_chat_log (document_id, user_id, query, response)
                           VALUES (%s, %s, %s, %s)""",
                        (document_id, user_id, query, response),
                    )
                return
            except (psycopg2.OperationalError, psycopg2.InterfaceError):
                if attempt == 2:
                    raise
                # Force a fresh pool on next get_cursor() — recycles any stale
                # sockets Neon closed on its end.
                try:
                    if self._pool is not None:
                        self._pool.closeall()
                except Exception:
                    pass
                self._pool = None

    def save_external_conversation(self, *, document_id: str, user_id: int | None,
                                   conversation_id: str, profile_key: str | None,
                                   assistant: str | None, channel: str, fmt: str, raw: str,
                                   exchanges: list[tuple[str, str]]) -> int:
        """Store a conversation held in an outside LLM.

        Keeps the submission verbatim and writes its exchanges to the Q&A log,
        replacing the exchanges saved earlier under the same conversation id
        (an earlier, shorter copy of the same chat). Returns how many exchanges
        were replaced.
        """
        with self.get_cursor() as cur:
            cur.execute(
                """INSERT INTO external_conversation
                       (document_id, user_id, conversation_id, profile_key, assistant,
                        channel, format, exchange_count, raw)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (document_id, user_id, conversation_id, profile_key, assistant,
                 channel, fmt, len(exchanges), raw),
            )
            cur.execute(
                """DELETE FROM lesson_chat_log
                   WHERE document_id = %s AND conversation_id = %s AND origin = 'external'
                     AND user_id IS NOT DISTINCT FROM %s""",
                (document_id, conversation_id, user_id),
            )
            replaced = cur.rowcount
            for query, response in exchanges:
                cur.execute(
                    """INSERT INTO lesson_chat_log
                           (document_id, user_id, query, response, origin, assistant,
                            conversation_id, profile_key)
                       VALUES (%s, %s, %s, %s, 'external', %s, %s, %s)""",
                    (document_id, user_id, query, response, assistant,
                     conversation_id, profile_key),
                )
            return replaced

    def get_lesson_chat_history(self, document_id: str, user_id: int | None,
                                limit: int = 200) -> list[dict]:
        """Return chronological Q&A turns for a (document, user).

        When user_id is None, returns rows for unauthenticated chats only.
        Use get_lesson_chat_history_all() if you need every user's turns
        (superuser view).
        """
        with self.get_cursor(commit=False) as cur:
            if user_id is None:
                cur.execute(
                    """SELECT id, query, response, created_at, origin, assistant, conversation_id
                       FROM lesson_chat_log
                       WHERE document_id = %s AND user_id IS NULL
                       ORDER BY created_at, id LIMIT %s""",
                    (document_id, limit),
                )
            else:
                cur.execute(
                    """SELECT id, query, response, created_at, origin, assistant, conversation_id
                       FROM lesson_chat_log
                       WHERE document_id = %s AND user_id = %s
                       ORDER BY created_at, id LIMIT %s""",
                    (document_id, user_id, limit),
                )
            return [dict(r) for r in cur.fetchall()]

    def get_lesson_chat_history_all(self, document_id: str, limit: int = 500) -> list[dict]:
        """Return every chat turn on a document, across all users. Superuser only."""
        with self.get_cursor(commit=False) as cur:
            cur.execute(
                """SELECT user_id, id, query, response, created_at, origin, assistant, conversation_id
                   FROM lesson_chat_log
                   WHERE document_id = %s
                   ORDER BY created_at, id LIMIT %s""",
                (document_id, limit),
            )
            return [dict(r) for r in cur.fetchall()]

    def get_chat_lessons_summary(self, user_id: int | None,
                                 all_users: bool = False,
                                 limit: int = 200) -> list[dict]:
        """List documents with chat activity, most-recently-active first.

        Returns: [{document_id, title, last_chat_at, turn_count, user_id?}]
        If all_users=True (superuser), groups by (document_id, user_id) so a
        single lesson shows up once per chatting user.
        """
        with self.get_cursor(commit=False) as cur:
            if all_users:
                cur.execute(
                    """SELECT l.document_id,
                              l.user_id,
                              u.email as user_email,
                              d.title,
                              MAX(l.created_at) as last_chat_at,
                              COUNT(*) as turn_count
                       FROM lesson_chat_log l
                       LEFT JOIN document d ON d.id::text = l.document_id
                       LEFT JOIN users u ON u.id = l.user_id
                       WHERE l.document_id <> ''
                       GROUP BY l.document_id, l.user_id, u.email, d.title
                       ORDER BY MAX(l.created_at) DESC
                       LIMIT %s""",
                    (limit,),
                )
            elif user_id is None:
                cur.execute(
                    """SELECT l.document_id,
                              d.title,
                              MAX(l.created_at) as last_chat_at,
                              COUNT(*) as turn_count
                       FROM lesson_chat_log l
                       LEFT JOIN document d ON d.id::text = l.document_id
                       WHERE l.user_id IS NULL AND l.document_id <> ''
                       GROUP BY l.document_id, d.title
                       ORDER BY MAX(l.created_at) DESC
                       LIMIT %s""",
                    (limit,),
                )
            else:
                cur.execute(
                    """SELECT l.document_id,
                              d.title,
                              MAX(l.created_at) as last_chat_at,
                              COUNT(*) as turn_count
                       FROM lesson_chat_log l
                       LEFT JOIN document d ON d.id::text = l.document_id
                       WHERE l.user_id = %s AND l.document_id <> ''
                       GROUP BY l.document_id, d.title
                       ORDER BY MAX(l.created_at) DESC
                       LIMIT %s""",
                    (user_id, limit),
                )
            return [dict(r) for r in cur.fetchall()]

    def get_recent_lesson_views(self, user_id: int | None = None, limit: int = 50):
        """Get recent lesson views, optionally filtered by user."""
        with self.get_cursor() as cur:
            if user_id is not None:
                cur.execute(
                    """SELECT lv.document_id, d.title, lv.created_at
                       FROM lesson_view lv
                       LEFT JOIN document d ON d.id::text = lv.document_id
                       WHERE lv.user_id = %s
                       ORDER BY lv.created_at DESC
                       LIMIT %s""",
                    (user_id, limit),
                )
            else:
                cur.execute(
                    """SELECT lv.document_id, lv.user_id, d.title, lv.created_at
                       FROM lesson_view lv
                       LEFT JOIN document d ON d.id::text = lv.document_id
                       ORDER BY lv.created_at DESC
                       LIMIT %s""",
                    (limit,),
                )
            return cur.fetchall()
