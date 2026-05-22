"""Lesson metadata, browsing, and content retrieval."""

import json
from typing import Optional


class LessonMetadataMixin:
    """Lesson field updates, category browsing, and content retrieval."""

    def update_document_lesson_fields(
        self,
        doc_id: str,
        lesson_date: Optional[str] = None,
        lesson_index: Optional[str] = None,
    ):
        """Update lesson_date and lesson_index for a document."""
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                UPDATE document
                SET lesson_date = %s, lesson_index = %s, updated_at = NOW()
                WHERE id = %s
                """,
                (lesson_date, lesson_index, doc_id),
            )

    def get_lesson_categories(self) -> list[dict]:
        """Get document sources that have at least one transcribed document.

        Returns list of dicts with: source_id, source_name, category_name, lesson_count.
        """
        if self.allowed_source_ids is None:
            source_filter = ""
            params: list = []
        else:
            source_filter = " AND ds.id = ANY(%s)"
            params = [list(self.allowed_source_ids)]
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(f"""
                SELECT ds.id as source_id, ds.name as source_name,
                       ds.category_name as source_category_name,
                       COUNT(DISTINCT d.id) as lesson_count
                FROM document_source ds
                JOIN document d ON d.source_id = ds.id
                JOIN analysis a ON a.document_id = d.id AND a.type_id = 1
                WHERE ds.visible = TRUE{source_filter}
                GROUP BY ds.id, ds.name, ds.category_name
                ORDER BY ds.id
            """, params)
            return [dict(row) for row in cursor.fetchall()]

    def get_source_lessons(self, source_id: int) -> list[dict]:
        """Get documents with content for a source.

        Includes documents that have a transcription (type_id=1 analysis)
        OR text documents (document type_id=1) which have content directly.

        Returns list of dicts with: id, title, category_name, created_at.
        Uses tiered override: document.category_name > source.category_name > source.name.
        """
        with self.get_cursor(commit=False) as cursor:
            cursor.execute("""
                SELECT d.id, d.title, d.category_name,
                       d.lesson_date, d.lesson_index, d.created_at,
                       d.metadata->'scraper'->>'instructor' as instructor,
                       d.metadata->>'lesson_type' as lesson_type,
                       d.source_url
                FROM document d
                WHERE d.source_id = %s
                  AND (d.type_id = 1
                       OR EXISTS (SELECT 1 FROM analysis a
                                  WHERE a.document_id = d.id AND a.type_id = 1))
                ORDER BY d.lesson_date NULLS LAST, d.lesson_index NULLS LAST, d.title
            """, (source_id,))
            return [dict(row) for row in cursor.fetchall()]

    def get_fmtt_lessons(self) -> list[dict]:
        """All FMTT (source_id=3) lessons, with the metadata the /fmtt page needs.

        Pulls the segment_label out of metadata->lesson_page so the page can
        group lessons under their canonical segment heading. The lesson_type
        falls back to the LLM-extracted value (Segments 11-12 weren't loaded
        through the scraper path that populates metadata.lesson_type).
        """
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(
                """SELECT d.id::text as id,
                          d.title,
                          d.lesson_date::text as lesson_date,
                          d.origin,
                          d.presenter,
                          d.metadata->'lesson_page'->>'segment_label' as segment_label,
                          COALESCE(
                              d.metadata->>'lesson_type',
                              d.metadata->'lesson_page'->'extracted'->>'lesson_type'
                          ) as lesson_type
                   FROM document d
                   WHERE d.source_id = 3
                   ORDER BY d.lesson_date NULLS LAST, d.title""",
            )
            return [dict(r) for r in cursor.fetchall()]

    def get_document_transcription(self, document_id: str) -> Optional[str]:
        """Get the text content for a document.

        Works for both transcribed audio/video and text documents --
        both store their content as analysis type_id=1.
        """
        with self.get_cursor(commit=False) as cursor:
            cursor.execute("""
                SELECT a.data->>'text' as text
                FROM analysis a
                WHERE a.document_id = %s AND a.type_id = 1
            """, (document_id,))
            row = cursor.fetchone()
            return row["text"] if row else None

    def get_document_whisper_segments(self, document_id: str) -> Optional[list]:
        """Get whisper segments for a document from its transcription analysis."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute("""
                SELECT a.data->'segments' as segments
                FROM analysis a
                WHERE a.document_id = %s AND a.type_id = 1
            """, (document_id,))
            row = cursor.fetchone()
            if row and row["segments"]:
                return row["segments"] if isinstance(row["segments"], list) else json.loads(row["segments"])
            return None

    def get_document_whisper_words(self, document_id: str) -> Optional[list]:
        """Get word-level timestamps for a document from its transcription analysis."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute("""
                SELECT a.data->'words' as words
                FROM analysis a
                WHERE a.document_id = %s AND a.type_id = 1
            """, (document_id,))
            row = cursor.fetchone()
            if row and row["words"]:
                return row["words"] if isinstance(row["words"], list) else json.loads(row["words"])
            return None

    def get_lesson_info(self, document_id: str) -> Optional[dict]:
        """Get lesson info for the detail page."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute("""
                SELECT d.id::text as document_id, d.title,
                       ds.name as source_name, d.source_url,
                       a.data->>'text' as text,
                       (d.s3_key IS NOT NULL) as has_audio
                FROM document d
                JOIN document_source ds ON ds.id = d.source_id
                LEFT JOIN analysis a ON a.document_id = d.id AND a.type_id = 1
                WHERE d.id = %s
            """, (document_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
