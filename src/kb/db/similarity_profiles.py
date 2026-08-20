"""Persistence and existing-analysis inputs for similarity profiles."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

from psycopg2.extras import execute_values

if TYPE_CHECKING:
    from kb.similarity.profiles import ProjectedProfile


CACHE_SUFFIXES = {
    "decompose": ":decompose_v3",
    "somatic": ":somatic_v2",
    "language": ":language_v1",
    "concepts": ":concept_tags_v2",
    "spatial": ":spatial_v2",
}


def _cache_key(transcript: str, suffix: str) -> str:
    return hashlib.sha256((transcript.strip() + suffix).encode()).hexdigest()[:16]


class SimilarityProfilesMixin:
    """Build-input reads and atomic replacement of derived profiles."""

    def get_similarity_projection_inputs(self) -> list[dict]:
        """Read eligible ATMs and their already-cached pass0b artifacts.

        This is deliberately a read-only projection boundary. It never invokes
        an analyzer or creates analysis-queue work.
        """
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(
                """
                SELECT d.id::text AS document_id,
                       lr.lesson_key,
                       tx.data->>'text' AS transcript,
                       COALESCE((d.metadata->>'duration_seconds')::integer, 0) AS duration_seconds,
                       le.embedding::text AS embedding
                FROM lesson_rendition lr
                JOIN document d ON d.id = lr.document_id
                JOIN LATERAL (
                    SELECT data FROM analysis
                    WHERE document_id = d.id AND type_id = 1
                    ORDER BY updated_at DESC LIMIT 1
                ) tx ON TRUE
                LEFT JOIN lesson_embedding le
                  ON le.document_id = d.id AND le.method = 'default'
                ORDER BY d.id
                """
            )
            documents = [dict(row) for row in cursor.fetchall()]

        keys = {
            _cache_key(row["transcript"], suffix)
            for row in documents
            for suffix in CACHE_SUFFIXES.values()
        }
        cache = {}
        if keys:
            with self.get_cursor(commit=False) as cursor:
                cursor.execute("SELECT key, data FROM cache WHERE key = ANY(%s)", (sorted(keys),))
                cache = {row["key"]: dict(row["data"]) for row in cursor.fetchall()}

        for row in documents:
            transcript = row.pop("transcript")
            row["duration_seconds"] = row["duration_seconds"] or None
            raw_embedding = row.get("embedding")
            row["embedding"] = [float(value) for value in json.loads(raw_embedding)] if raw_embedding else None
            for name, suffix in CACHE_SUFFIXES.items():
                row[name] = cache.get(_cache_key(transcript, suffix))
        return documents

    def replace_similarity_profiles(
        self,
        profiles: list["ProjectedProfile"],
        *,
        weight_set: str,
        content_hashes: dict[str, str],
    ) -> None:
        """Atomically replace the selected version of derived profiles."""
        from kb.similarity.profiles import profile_to_payload

        if not profiles:
            return
        version = profiles[0].schema_version
        extraction_model = profiles[0].extraction_model
        if any(
            item.schema_version != version or item.extraction_model != extraction_model
            for item in profiles
        ):
            raise ValueError("profile batch must use one schema and extraction model")
        rows = [
            (
                item.profile.document_id,
                item.profile.lesson_key,
                item.schema_version,
                item.extraction_model,
                weight_set,
                content_hashes[item.profile.document_id],
                json.dumps(profile_to_payload(item)),
                item.profile.duration_seconds,
            )
            for item in profiles
        ]
        with self.get_cursor() as cursor:
            cursor.execute(
                "DELETE FROM lesson_profile WHERE schema_version = %s AND extraction_model = %s AND weight_set = %s",
                (version, extraction_model, weight_set),
            )
            execute_values(
                cursor,
                """
                INSERT INTO lesson_profile
                    (document_id, lesson_key, schema_version, extraction_model,
                     weight_set, content_hash, profile, duration_seconds)
                VALUES %s
                """,
                rows,
            )
