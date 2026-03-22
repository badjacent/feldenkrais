"""Book/booklet viewer, analyzer, explore, and book-specific pipeline dashboard.

This is a thin re-export shim.  The real implementation lives in the
``book`` package (``src/kb/book/``).  This file exists so that existing
imports in ``web.py`` continue to work:

    from web_book import create_book_router
    from web_book import _build_book_pipeline_section
    from web_book import process_job
"""

from book import create_book_router, _build_book_pipeline_section, process_job

__all__ = [
    "create_book_router",
    "_build_book_pipeline_section",
    "process_job",
]
