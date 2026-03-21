"""Embedding and concept search operations."""

import json


class EmbeddingsMixin:
    """Lesson embeddings, concept tags, and vector search."""

    def upsert_embedding(self, document_id: str, embedding: list[float],
                         summary_text: str, summary_hash: str, method: str = "default"):
        """Insert or update a lesson embedding."""
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO lesson_embedding (document_id, method, embedding, summary_text, summary_hash, updated_at)
                VALUES (%s, %s, %s::vector, %s, %s, NOW())
                ON CONFLICT (document_id, method) DO UPDATE SET
                    embedding = EXCLUDED.embedding,
                    summary_text = EXCLUDED.summary_text,
                    summary_hash = EXCLUDED.summary_hash,
                    updated_at = NOW()
                """,
                (document_id, method, str(embedding), summary_text, summary_hash),
            )

    def search_lessons(self, query_embedding: list[float], limit: int = 10, method: str = "default") -> list[dict]:
        """Search lessons by cosine similarity. Returns list of {document_id, title, similarity, summary_text, source_id, category_name}."""
        if self.allowed_source_ids is not None:
            source_join = "\nJOIN document_source ds ON ds.id = d.source_id"
            source_filter = " AND ds.id = ANY(%s)"
            extra_params: list = [list(self.allowed_source_ids)]
        else:
            source_join = ""
            source_filter = ""
            extra_params = []
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(
                f"""
                SELECT le.document_id::text, d.title,
                       1 - (le.embedding <=> %s::vector) as similarity,
                       le.summary_text,
                       d.source_id,
                       d.category_name
                FROM lesson_embedding le
                JOIN document d ON d.id = le.document_id{source_join}
                WHERE le.method = %s{source_filter}
                ORDER BY le.embedding <=> %s::vector
                LIMIT %s
                """,
                [str(query_embedding), method] + extra_params + [str(query_embedding), limit],
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_embedding_hashes(self, method: str = "default") -> dict[str, str]:
        """Return {document_id: summary_hash} for all embedded lessons."""
        if self.allowed_source_ids is not None:
            query = """
                SELECT le.document_id::text, le.summary_hash
                FROM lesson_embedding le
                JOIN document d ON d.id = le.document_id
                JOIN document_source ds ON ds.id = d.source_id
                WHERE le.method = %s AND ds.id = ANY(%s)
            """
            params: tuple = (method, list(self.allowed_source_ids))
        else:
            query = "SELECT document_id::text, summary_hash FROM lesson_embedding WHERE method = %s"
            params = (method,)
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(query, params)
            return {row["document_id"]: row["summary_hash"] for row in cursor.fetchall()}

    # Lesson concept operations

    def upsert_concepts(self, document_id: str, concepts: list[dict]):
        """Replace all concepts for a lesson. concepts: [{"category": ..., "value": ...}, ...]"""
        with self.get_cursor() as cursor:
            cursor.execute("DELETE FROM lesson_concept WHERE document_id = %s", (document_id,))
            if concepts:
                from psycopg2.extras import execute_values
                execute_values(
                    cursor,
                    "INSERT INTO lesson_concept (document_id, category, value) VALUES %s",
                    [(document_id, c["category"], c["value"]) for c in concepts],
                )

    def upsert_spatial_concepts(self, document_id: str, concepts: list[dict]):
        """Replace spatial concepts for a lesson (category-scoped delete).

        Only deletes/replaces rows with spatial categories, leaving existing
        concept rows (body_region, movement_pattern, etc.) untouched.

        concepts: [{"category": ..., "value": ...}, ...]
        """
        spatial_categories = ("spatial_config", "contact_point", "constraint", "working_position")
        with self.get_cursor() as cursor:
            cursor.execute(
                "DELETE FROM lesson_concept WHERE document_id = %s AND category = ANY(%s)",
                (document_id, list(spatial_categories)),
            )
            if concepts:
                from psycopg2.extras import execute_values
                execute_values(
                    cursor,
                    "INSERT INTO lesson_concept (document_id, category, value) VALUES %s",
                    [(document_id, c["category"], c["value"]) for c in concepts],
                )

    def search_by_concepts(self, terms: list[str], limit: int = 10) -> list[dict]:
        """Search lessons by matching concept values. Ranks by number of matching concepts.

        Returns list of {document_id, title, match_count, matched_concepts, source_id, category_name}.
        """
        if not terms:
            return []
        # Build ILIKE conditions for each search term
        conditions = []
        params = []
        for term in terms:
            conditions.append("lc.value ILIKE %s")
            params.append(f"%{term}%")

        where_clause = " OR ".join(conditions)
        if self.allowed_source_ids is not None:
            source_join = "\nJOIN document_source ds ON ds.id = d.source_id"
            source_filter = " AND ds.id = ANY(%s)"
            params.append(list(self.allowed_source_ids))
        else:
            source_join = ""
            source_filter = ""

        with self.get_cursor(commit=False) as cursor:
            cursor.execute(
                f"""
                SELECT lc.document_id::text, d.title,
                       COUNT(*) as match_count,
                       ARRAY_AGG(DISTINCT lc.category || ':' || lc.value) as matched_concepts,
                       d.source_id,
                       d.category_name
                FROM lesson_concept lc
                JOIN document d ON d.id = lc.document_id{source_join}
                WHERE ({where_clause}){source_filter}
                GROUP BY lc.document_id, d.title, d.source_id, d.category_name
                ORDER BY COUNT(*) DESC
                LIMIT %s
                """,
                params + [limit],
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_lesson_concepts(self, document_id: str) -> list[dict]:
        """Return concepts for a lesson as [{category, value}, ...]."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(
                "SELECT category, value FROM lesson_concept WHERE document_id = %s ORDER BY category, value",
                (document_id,),
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
