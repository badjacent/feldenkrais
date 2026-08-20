"""Persistence boundary for pre-scoring rendition groups."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from psycopg2.extras import execute_values

if TYPE_CHECKING:
    from kb.similarity.renditions import RenditionBuild


def _vector_values(value) -> list[float] | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = json.loads(value)
    return [float(item) for item in value]


class RenditionsMixin:
    """Read rendition inputs and atomically replace the derived gate state."""

    def get_rendition_inputs(self) -> list[dict]:
        """Return transcribed documents plus optional raw-transcript vectors.

        The canonical-summary `default` vector is intentionally not accepted as
        raw evidence. Only embeddings stored with method `raw` participate in
        the >0.985 rendition rule.
        """
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(
                """
                SELECT d.id::text AS document_id,
                       d.title,
                       COALESCE(d.metadata, '{}'::jsonb) AS metadata,
                       tx.data->>'text' AS transcript,
                       raw.embedding::text AS raw_embedding
                FROM document d
                JOIN LATERAL (
                    SELECT a.data
                    FROM analysis a
                    WHERE a.document_id = d.id AND a.type_id = 1
                    ORDER BY a.updated_at DESC
                    LIMIT 1
                ) tx ON TRUE
                LEFT JOIN lesson_embedding raw
                  ON raw.document_id = d.id AND raw.method = 'raw'
                ORDER BY d.id
                """
            )
            return [
                {
                    **dict(row),
                    "metadata": row["metadata"] or {},
                    "raw_embedding": _vector_values(row["raw_embedding"]),
                }
                for row in cursor.fetchall()
            ]

    def get_existing_lesson_keys(self) -> dict[str, str]:
        with self.get_cursor(commit=False) as cursor:
            cursor.execute("SELECT document_id::text, lesson_key FROM lesson_rendition")
            return {row["document_id"]: row["lesson_key"] for row in cursor.fetchall()}

    def replace_rendition_gate(self, build: "RenditionBuild") -> None:
        """Atomically replace all derived assignments and pair evidence."""
        assignments = [
            (document_id, lesson_key, build.detection_version)
            for document_id, lesson_key in sorted(build.lesson_keys.items())
        ]
        evidence = [
            (
                item.left_id,
                item.right_id,
                build.detection_version,
                list(item.reasons),
                item.shingle_overlap,
                item.raw_cosine,
                item.canonical_identity,
            )
            for item in build.evidence
        ]
        with self.get_cursor() as cursor:
            cursor.execute("DELETE FROM lesson_rendition_evidence")
            cursor.execute("DELETE FROM lesson_rendition")
            if assignments:
                execute_values(
                    cursor,
                    """
                    INSERT INTO lesson_rendition
                        (document_id, lesson_key, detection_version)
                    VALUES %s
                    """,
                    assignments,
                )
            if evidence:
                execute_values(
                    cursor,
                    """
                    INSERT INTO lesson_rendition_evidence
                        (left_document_id, right_document_id, detection_version,
                         reasons, shingle_overlap, raw_cosine, canonical_identity)
                    VALUES %s
                    """,
                    evidence,
                )

    def get_renditions(self, document_id: str) -> list[dict]:
        """Return the explicit rendition relation, excluding the query member."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(
                """
                SELECT peer.document_id::text,
                       d.title,
                       peer.lesson_key,
                       peer.detection_version
                FROM lesson_rendition query
                JOIN lesson_rendition peer ON peer.lesson_key = query.lesson_key
                JOIN document d ON d.id = peer.document_id
                WHERE query.document_id = %s
                  AND peer.document_id <> query.document_id
                ORDER BY d.title, peer.document_id
                """,
                (document_id,),
            )
            return [dict(row) for row in cursor.fetchall()]
