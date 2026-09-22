"""Provider-specific document sources for imported analysis corpora."""

ASTRA_PROVIDER = "codex-astra"
ASTRA_SUFFIX = "-Astra"


def document_analysis_provider(db, document_id: str) -> str | None:
    """Return the provider encoded by a document's source name.

    Gemini is represented by ``None`` because it uses the historical,
    unqualified cache keys.  Astra caches include the provider key so their
    results cannot collide with Gemini results for the same transcript.
    """
    with db.get_cursor(commit=False) as cursor:
        cursor.execute(
            """
            SELECT ds.name
            FROM document d
            JOIN document_source ds ON ds.id = d.source_id
            WHERE d.id = %s
            """,
            (document_id,),
        )
        row = cursor.fetchone()
    if not row:
        return None
    return ASTRA_PROVIDER if row["name"].endswith(ASTRA_SUFFIX) else None


def provider_cache_suffix(provider_key: str | None) -> str:
    """Return the suffix appended to an analysis cache discriminator."""
    return f":{provider_key}" if provider_key else ""


def analysis_source_name(base_name: str, provider_key: str | None) -> str:
    """Return the source name for an analysis provider.

    Gemini is the default and keeps the source name unchanged. Astra sources
    carry a visible suffix while document titles remain provider-neutral.
    """
    name = base_name.strip()
    if not name:
        raise ValueError("base source name must not be empty")

    if provider_key == ASTRA_PROVIDER:
        return name if name.endswith(ASTRA_SUFFIX) else name + ASTRA_SUFFIX
    if name.endswith(ASTRA_SUFFIX):
        return name.removesuffix(ASTRA_SUFFIX)
    return name


def get_or_create_analysis_source(
    db,
    base_name: str,
    provider_key: str | None,
    *,
    category_name: str | None = None,
    visible: bool = True,
    public: bool = False,
) -> int:
    """Resolve an idempotent ``document_source`` for an analyzed import."""
    source_name = analysis_source_name(base_name, provider_key)
    display_name = analysis_source_name(category_name or base_name, provider_key)

    with db.get_cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO document_source (name, category_name, visible, public)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (name) DO UPDATE SET
                category_name = COALESCE(document_source.category_name, EXCLUDED.category_name)
            RETURNING id
            """,
            (source_name, display_name, visible, public),
        )
        return cursor.fetchone()["id"]


def ensure_document_analysis_source(db, document_id: str, provider_key: str | None) -> int:
    """Move a document to the provider-specific variant of its current source."""
    with db.get_cursor() as cursor:
        cursor.execute(
            """
            SELECT ds.id, ds.name, ds.category_name, ds.visible, ds.public
            FROM document d
            JOIN document_source ds ON ds.id = d.source_id
            WHERE d.id = %s
            FOR UPDATE OF d
            """,
            (document_id,),
        )
        current = cursor.fetchone()
        if not current:
            raise ValueError(f"Document not found: {document_id}")

        target_name = analysis_source_name(current["name"], provider_key)
        if target_name == current["name"]:
            return current["id"]

        target_category = analysis_source_name(
            current.get("category_name") or current["name"], provider_key
        )
        cursor.execute(
            """
            INSERT INTO document_source (name, category_name, visible, public)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (name) DO UPDATE SET
                category_name = COALESCE(document_source.category_name, EXCLUDED.category_name)
            RETURNING id
            """,
            (target_name, target_category, current["visible"], current["public"]),
        )
        target_id = cursor.fetchone()["id"]
        cursor.execute(
            "UPDATE document SET source_id = %s, updated_at = NOW() WHERE id = %s",
            (target_id, document_id),
        )
        return target_id
