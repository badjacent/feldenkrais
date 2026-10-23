"""Embedding and concept search operations."""

import json

from .analysis_profiles import CANONICAL_PROFILE_SQL


class EmbeddingsMixin:
    """Lesson embeddings, concept tags, and vector search."""

    def upsert_embedding(self, document_id: str, embedding: list[float],
                         summary_text: str, summary_hash: str, method: str = "default",
                         profile_key: str | None = None):
        """Insert or update a lesson embedding for a profile (canonical by default)."""
        with self.get_cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO lesson_embedding
                    (document_id, profile_key, method, embedding, summary_text, summary_hash, updated_at)
                VALUES (%s, COALESCE(%s, {CANONICAL_PROFILE_SQL}), %s, %s::vector, %s, %s, NOW())
                ON CONFLICT (document_id, profile_key, method) DO UPDATE SET
                    embedding = EXCLUDED.embedding,
                    summary_text = EXCLUDED.summary_text,
                    summary_hash = EXCLUDED.summary_hash,
                    updated_at = NOW()
                """,
                (document_id, profile_key, method, str(embedding), summary_text, summary_hash),
            )

    def search_lessons(
        self,
        query_embedding: list[float],
        limit: int = 10,
        method: str = "default",
        query_document_id: str | None = None,
    ) -> list[dict]:
        """Search embeddings, optionally enforcing the rendition gate.

        Supplying `query_document_id` excludes both the query document and all
        documents sharing its persisted `lesson_key`.
        """
        if self.allowed_source_ids is not None:
            source_join = "\nJOIN document_source ds ON ds.id = d.source_id"
            source_filter = " AND ds.id = ANY(%s)"
            extra_params: list = [list(self.allowed_source_ids)]
        else:
            source_join = ""
            source_filter = ""
            extra_params = []
        if query_document_id is not None:
            rendition_join = """
                LEFT JOIN lesson_rendition candidate_rendition
                  ON candidate_rendition.document_id = le.document_id
                LEFT JOIN lesson_rendition query_rendition
                  ON query_rendition.document_id = %s
            """
            rendition_filter = """
                AND le.document_id <> %s
                AND (
                    query_rendition.lesson_key IS NULL
                    OR candidate_rendition.lesson_key IS DISTINCT FROM query_rendition.lesson_key
                )
            """
            rendition_params: list = [query_document_id]
            rendition_filter_params: list = [query_document_id]
        else:
            rendition_join = ""
            rendition_filter = ""
            rendition_params = []
            rendition_filter_params = []
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(
                f"""
                SELECT le.document_id::text, d.title,
                       1 - (le.embedding <=> %s::vector) as similarity,
                       le.summary_text,
                       d.source_id,
                       d.category_name
                FROM lesson_embedding le
                JOIN document d ON d.id = le.document_id{source_join}{rendition_join}
                WHERE le.method = %s AND le.profile_key = {CANONICAL_PROFILE_SQL}{source_filter}{rendition_filter}
                ORDER BY le.embedding <=> %s::vector
                LIMIT %s
                """,
                [str(query_embedding)]
                + rendition_params
                + [method]
                + extra_params
                + rendition_filter_params
                + [str(query_embedding), limit],
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_embedding_hashes(self, method: str = "default") -> dict[str, str]:
        """Return {document_id: summary_hash} for all lessons embedded under the canonical profile."""
        if self.allowed_source_ids is not None:
            query = f"""
                SELECT le.document_id::text, le.summary_hash
                FROM lesson_embedding le
                JOIN document d ON d.id = le.document_id
                JOIN document_source ds ON ds.id = d.source_id
                WHERE le.method = %s AND le.profile_key = {CANONICAL_PROFILE_SQL}
                  AND ds.id = ANY(%s)
            """
            params: tuple = (method, list(self.allowed_source_ids))
        else:
            query = (
                "SELECT document_id::text, summary_hash FROM lesson_embedding "
                f"WHERE method = %s AND profile_key = {CANONICAL_PROFILE_SQL}"
            )
            params = (method,)
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(query, params)
            return {row["document_id"]: row["summary_hash"] for row in cursor.fetchall()}

    # Lesson concept operations

    def upsert_concepts(self, document_id: str, concepts: list[dict],
                        profile_key: str | None = None):
        """Replace a profile's concepts for a lesson (canonical by default).

        concepts: [{"category": ..., "value": ...}, ...]
        """
        profile_key = profile_key or self.get_canonical_profile()["key"]
        with self.get_cursor() as cursor:
            cursor.execute(
                "DELETE FROM lesson_concept WHERE document_id = %s AND profile_key = %s",
                (document_id, profile_key),
            )
            if concepts:
                from psycopg2.extras import execute_values
                execute_values(
                    cursor,
                    "INSERT INTO lesson_concept (document_id, profile_key, category, value) VALUES %s",
                    [(document_id, profile_key, c["category"], c["value"]) for c in concepts],
                )

    def upsert_spatial_concepts(self, document_id: str, concepts: list[dict],
                                profile_key: str | None = None):
        """Replace spatial concepts for a lesson (category-scoped delete).

        Only deletes/replaces rows with spatial categories, leaving existing
        concept rows (body_region, movement_pattern, etc.) untouched.

        concepts: [{"category": ..., "value": ...}, ...]
        """
        spatial_categories = ("spatial_config", "contact_point", "constraint", "working_position")
        profile_key = profile_key or self.get_canonical_profile()["key"]
        with self.get_cursor() as cursor:
            cursor.execute(
                "DELETE FROM lesson_concept "
                "WHERE document_id = %s AND profile_key = %s AND category = ANY(%s)",
                (document_id, profile_key, list(spatial_categories)),
            )
            if concepts:
                from psycopg2.extras import execute_values
                execute_values(
                    cursor,
                    "INSERT INTO lesson_concept (document_id, profile_key, category, value) VALUES %s",
                    [(document_id, profile_key, c["category"], c["value"]) for c in concepts],
                )

    def search_by_concepts(self, terms: list[str], limit: int = 10) -> list[dict]:
        """Search lessons by matching concept values. Ranks by weighted match count.

        Full-phrase matches (all terms appearing together in one concept value)
        are weighted 5x higher than individual term matches.

        Returns list of {document_id, title, match_count, matched_concepts, source_id, category_name}.
        """
        if not terms:
            return []

        # Build ILIKE conditions for each individual term
        term_conditions = []
        term_params = []
        for term in terms:
            term_conditions.append("lc.value ILIKE %s")
            term_params.append(f"%{term}%")

        # Also match the full phrase (all terms together) with higher weight
        full_phrase = " ".join(terms)
        phrase_weight = 5

        if self.allowed_source_ids is not None:
            source_join = "\nJOIN document_source ds ON ds.id = d.source_id"
            source_filter = " AND ds.id = ANY(%s)"
        else:
            source_join = ""
            source_filter = ""

        where_clause = " OR ".join(term_conditions)
        phrase_pattern = f"%{full_phrase}%"

        # Params: phrase_pattern, term_params..., [source_ids], phrase_pattern, limit
        params: list = [phrase_pattern] + term_params
        if self.allowed_source_ids is not None:
            params.append(list(self.allowed_source_ids))
        params.extend([phrase_pattern, limit])

        with self.get_cursor(commit=False) as cursor:
            cursor.execute(
                f"""
                SELECT lc.document_id::text, d.title,
                       (COUNT(*) + {phrase_weight} * COUNT(*) FILTER (WHERE lc.value ILIKE %s))::integer as match_count,
                       ARRAY_AGG(DISTINCT lc.category || ':' || lc.value) as matched_concepts,
                       d.source_id,
                       d.category_name
                FROM lesson_concept lc
                JOIN document d ON d.id = lc.document_id{source_join}
                WHERE ({where_clause}) AND lc.profile_key = {CANONICAL_PROFILE_SQL}{source_filter}
                GROUP BY lc.document_id, d.title, d.source_id, d.category_name
                ORDER BY (COUNT(*) + {phrase_weight} * COUNT(*) FILTER (WHERE lc.value ILIKE %s)) DESC
                LIMIT %s
                """,
                params,
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_lesson_concepts(self, document_id: str, profile_key: str | None = None) -> list[dict]:
        """Return a profile's concepts for a lesson as [{category, value}, ...] (canonical by default)."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(
                f"""
                SELECT category, value FROM lesson_concept
                WHERE document_id = %s AND profile_key = COALESCE(%s, {CANONICAL_PROFILE_SQL})
                ORDER BY category, value
                """,
                (document_id, profile_key),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_docs_with_condensed(self, cache_suffix: str = ":condense_v4") -> list[dict]:
        """Find docs that have condensed analysis cached.

        Single SQL query to get all transcripts + titles, compute cache keys in
        Python, then bulk-check against cache keys. Returns list of
        {doc_id, title, cache_key} for docs with condensed data.
        """
        import hashlib

        if self.allowed_source_ids is not None:
            source_join = "\nJOIN document_source ds ON ds.id = d.source_id"
            source_filter = " AND ds.id = ANY(%s)"
            params: list = [list(self.allowed_source_ids)]
        else:
            source_join = ""
            source_filter = ""
            params = []

        # One query: get all transcript texts and doc titles
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(f"""
                SELECT a.document_id::text as doc_id,
                       d.title,
                       a.data->>'text' as text
                FROM analysis a
                JOIN document d ON d.id = a.document_id{source_join}
                WHERE a.type_id = 1 AND a.data->>'text' IS NOT NULL{source_filter}
            """, params)
            doc_keys = {}
            for row in cursor.fetchall():
                text = (row["text"] or "").strip()
                if text:
                    cache_key = hashlib.sha256(
                        (text + cache_suffix).encode()
                    ).hexdigest()[:16]
                    doc_keys[cache_key] = {
                        "doc_id": row["doc_id"],
                        "title": row["title"],
                    }

        # One query: get all cache keys
        all_keys = self.get_cache_keys()
        condensed_keys = set(doc_keys.keys()) & all_keys

        return [
            {**doc_keys[key], "cache_key": key}
            for key in condensed_keys
        ]

    def get_all_transcription_hashes(self) -> dict[str, str]:
        """Return {document_id: cache_key} for all documents with transcriptions.

        Computes SHA256 hashes in Python to match get_cache_key() in web.py.
        Single DB round-trip instead of one per document.
        """
        import hashlib
        if self.allowed_source_ids is not None:
            source_join = """
                JOIN document d ON d.id = a.document_id
                JOIN document_source ds ON ds.id = d.source_id"""
            source_filter = " AND ds.id = ANY(%s)"
            params: list = [list(self.allowed_source_ids)]
        else:
            source_join = ""
            source_filter = ""
            params = []
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(f"""
                SELECT a.document_id::text as doc_id, a.data->>'text' as text
                FROM analysis a{source_join}
                WHERE a.type_id = 1 AND a.data->>'text' IS NOT NULL{source_filter}
            """, params)
            result = {}
            for row in cursor.fetchall():
                text = row["text"].strip()
                if text:
                    result[row["doc_id"]] = hashlib.sha256(text.encode()).hexdigest()[:16]
            return result
