"""Book pipeline web interface package.

Re-exports the main entry points for backward compatibility:
    - ``create_book_router``  (used by web.py)
    - ``_build_book_pipeline_section``  (used by web.py)
    - ``process_job``  (used by web.py Lambda handler)
"""

from book.router import create_book_router
from book.pipeline import _build_book_pipeline_section
from book.analysis import process_job

__all__ = [
    "create_book_router",
    "_build_book_pipeline_section",
    "process_job",
]
