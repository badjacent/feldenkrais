"""Book router factory: assembles all book sub-module routes into one APIRouter."""

from fastapi import APIRouter

from book.state import init_state
from book.analysis import get_vl_booklet_intros


def create_book_router(db, data_dir) -> APIRouter:
    """Create and return the book/analyzer router.

    Called by web.py to conditionally mount book-specific routes.
    Uses ``init_state()`` to populate shared module-level state.
    """
    # Initialize shared state first
    init_state(db, data_dir)

    # Import route modules (they register decorators on import)
    from book import explore
    from book import booklet_routes
    from book import book_page
    from book import review_routes
    from book import admin_routes

    # Build a fresh router and include all sub-routes
    router = APIRouter()

    # Review
    router.add_api_route("/review", review_routes.review_page, methods=["GET"], response_class=review_routes.HTMLResponse)

    # Explore
    router.add_api_route("/explore", explore.explore_page, methods=["GET"], response_class=explore.HTMLResponse)
    router.add_api_route("/api/explore/load", explore.api_explore_load, methods=["POST"])
    router.add_api_route("/api/explore/round", explore.api_explore_round, methods=["POST"])

    # Booklet
    router.add_api_route("/booklet", booklet_routes.booklet_page, methods=["GET"], response_class=booklet_routes.HTMLResponse)

    # Book
    router.add_api_route("/book", book_page.book_page, methods=["GET"], response_class=book_page.HTMLResponse)

    # Admin / Pipeline API
    router.add_api_route("/admin/precache", admin_routes.admin_precache, methods=["POST"])
    router.add_api_route("/api/pipeline/status", admin_routes.pipeline_status, methods=["GET"])
    router.add_api_route("/api/pipeline/toggle/{node_name:path}", admin_routes.toggle_pipeline_node, methods=["POST"])
    router.add_api_route("/api/pipeline/rebuild/{node_name:path}", admin_routes.rebuild_pipeline_node, methods=["POST"])
    router.add_api_route("/api/pipeline/run/{lesson_id}", admin_routes.run_pipeline_for_lesson, methods=["POST"])
    router.add_api_route("/api/pipeline/book-lessons", admin_routes.add_book_lesson, methods=["POST"])
    router.add_api_route("/api/pipeline/book-lessons/remove", admin_routes.remove_book_lesson, methods=["POST"])
    router.add_api_route("/api/pipeline/book-lessons", admin_routes.list_book_lessons, methods=["GET"])

    # Re-initialize VL data now that DATA_DIR is set
    from book import booklet_data
    booklet_data.VL_BOOKLET_INTROS = get_vl_booklet_intros()

    # Initialize BOOK_LESSONS compat dict
    from book.book_page import _load_book_lessons_compat
    book_page.BOOK_LESSONS = _load_book_lessons_compat()

    return router
