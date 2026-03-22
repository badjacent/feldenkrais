"""Review page route."""

from fastapi.responses import HTMLResponse

from book.state import _db


async def review_page():
    """Display the latest book review from the pipeline."""
    if not _db:
        return HTMLResponse("<p>Database not configured.</p>", status_code=503)
    data = _db.get_cache("pipeline:review")
    if not data or "report" not in data:
        return HTMLResponse("<p>No review available yet. Run the pipeline to generate one.</p>")
    import markdown
    report_html = markdown.markdown(data["report"], extensions=["tables", "fenced_code"])
    return f"""<!DOCTYPE html><html><head><title>Book Review</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
       max-width: 900px; margin: 0 auto; padding: 20px; line-height: 1.7; }}
h1, h2, h3 {{ color: #333; }}
h2 {{ border-bottom: 1px solid #ddd; padding-bottom: 5px; }}
.nav {{ margin-bottom: 20px; }}
.nav a {{ color: #007bff; text-decoration: none; }}
.nav a:hover {{ text-decoration: underline; }}
table {{ border-collapse: collapse; width: 100%; margin: 10px 0; }}
th, td {{ border: 1px solid #ddd; padding: 6px 10px; text-align: left; }}
th {{ background: #f0f0f0; }}
</style></head><body>
<div class="nav"><a href="/">&larr; Back to Book</a> | <a href="/analyzer">Analyzer</a></div>
{report_html}
</body></html>"""

