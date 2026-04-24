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
        """Append a Q&A exchange to the lesson's chat log."""
        with self.get_cursor() as cur:
            cur.execute(
                """INSERT INTO lesson_chat_log (document_id, user_id, query, response)
                   VALUES (%s, %s, %s, %s)""",
                (document_id, user_id, query, response),
            )

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

    def get_lesson_chat_history(self, document_id: str, user_id: int | None = None,
                                limit: int = 50):
        """Get chat history for a lesson, optionally filtered by user."""
        with self.get_cursor() as cur:
            if user_id is not None:
                cur.execute(
                    """SELECT query, response, created_at
                       FROM lesson_chat_log
                       WHERE document_id = %s AND user_id = %s
                       ORDER BY created_at DESC
                       LIMIT %s""",
                    (document_id, user_id, limit),
                )
            else:
                cur.execute(
                    """SELECT user_id, query, response, created_at
                       FROM lesson_chat_log
                       WHERE document_id = %s
                       ORDER BY created_at DESC
                       LIMIT %s""",
                    (document_id, limit),
                )
            return cur.fetchall()
