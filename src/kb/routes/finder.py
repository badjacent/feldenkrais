"""Finder subdomain routes: search, stats, landing page HTML."""

import os

from fastapi import APIRouter
from fastapi.responses import JSONResponse

import web as _web

router = APIRouter()


@router.get("/api/finder/search")
async def finder_search(q: str, limit: int = 20):
    """Semantic search across external lessons."""
    settings = _web._db.get_cache("settings:llm_enabled") if _web._db else None
    if not settings or not settings.get("enabled", False):
        return JSONResponse({"error": "Search is temporarily disabled"}, status_code=503)
    import openai
    client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY", "").strip())
    resp = client.embeddings.create(model="text-embedding-3-small", input=[q])
    vec = resp.data[0].embedding

    with _web._db.get_cursor(commit=False) as cur:
        cur.execute("""
            SELECT el.title, el.url, el.description, el.tags, el.access,
                   el.content_type, ls.name as source_name,
                   1 - (el.embedding <=> %s::vector) as similarity
            FROM external_lesson el
            LEFT JOIN lesson_source ls ON ls.id = el.lesson_source_id
            WHERE el.embedding IS NOT NULL
            ORDER BY el.embedding <=> %s::vector
            LIMIT %s
        """, (str(vec), str(vec), limit))
        rows = cur.fetchall()

    return {
        "query": q,
        "results": [
            {
                "title": r["title"],
                "url": r["url"],
                "description": r["description"],
                "tags": r["tags"],
                "access": r["access"],
                "content_type": r["content_type"],
                "source_name": r["source_name"],
                "similarity": round(r["similarity"], 3),
            }
            for r in rows
        ],
    }


@router.get("/api/finder/stats")
async def finder_stats():
    """Return basic stats for the finder."""
    with _web._db.get_cursor(commit=False) as cur:
        cur.execute("SELECT COUNT(*) as total FROM external_lesson WHERE embedding IS NOT NULL")
        total = cur.fetchone()["total"]
        cur.execute("""
            SELECT COUNT(DISTINCT lesson_source_id) as sources
            FROM external_lesson WHERE embedding IS NOT NULL
        """)
        sources = cur.fetchone()["sources"]
    return {"total": total, "sources": sources}
