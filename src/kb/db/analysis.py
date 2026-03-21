"""Analysis record operations."""

from typing import Optional
from uuid import uuid4


class AnalysisMixin:
    """Create and retrieve analysis records."""

    def create_analysis(
        self,
        document_id: str,
        type_id: int,
        data: str,
    ) -> str:
        """Create a new analysis record. Returns the analysis ID."""
        analysis_id = str(uuid4())
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO analysis (id, document_id, type_id, data)
                VALUES (%s, %s, %s, %s::jsonb)
                """,
                (analysis_id, document_id, type_id, data),
            )
        return analysis_id

    def get_analysis(self, document_id: str, type_id: int) -> Optional[dict]:
        """Get an analysis by document ID and type."""
        with self.get_cursor(commit=False) as cursor:
            cursor.execute(
                "SELECT * FROM analysis WHERE document_id = %s AND type_id = %s",
                (document_id, type_id),
            )
            row = cursor.fetchone()
            return dict(row) if row else None
