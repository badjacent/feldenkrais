"""Shared module-level state for the book package.

All mutable globals live here so that sub-modules can read them
via ``from book.state import _db, DATA_DIR, ...``.  The
``init_state()`` function is called once by ``create_book_router()``.
"""

import os

# Module-level refs set by create_book_router()
_db = None
DATA_DIR = None
LESSONS_DIR = None
PROMPTS_DIR = None
FRAMEWORK_VERSION = None

# These will be imported from web.py at init time
get_available_lessons = None
load_lesson = None
load_lesson_metadata = None
load_whisper_segments = None
format_timestamp = None
align_pass0_with_whisper = None
render_debug_log = None
get_s3_client = None
save_job = None
load_job = None
invoke_lambda_async = None
_run_pipeline_for_lesson = None
_pipeline_run_lock = None
_is_pipeline_locked = None
ANALYSIS_TIMEOUT = None

# S3 configuration for async jobs
KB_BUCKET = os.environ.get("KB_BUCKET")
LAMBDA_FUNCTION_NAME = os.environ.get("AWS_LAMBDA_FUNCTION_NAME_SELF") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME")
IS_LAMBDA = bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))


def init_state(db, data_dir):
    """Populate module-level state from create_book_router() call."""
    import book.state as _self

    _self._db = db
    _self.DATA_DIR = data_dir
    _self.LESSONS_DIR = data_dir / "lessons"

    import analysis as _analysis_mod
    _self.FRAMEWORK_VERSION = _analysis_mod.FRAMEWORK_VERSION
    _self.PROMPTS_DIR = _analysis_mod.PROMPTS_DIR

    # Import shared functions from web (safe here — web.py is fully loaded)
    import web
    _self.get_available_lessons = web.get_available_lessons
    _self.load_lesson = web.load_lesson
    _self.load_lesson_metadata = web.load_lesson_metadata
    _self.load_whisper_segments = web.load_whisper_segments
    _self.format_timestamp = web.format_timestamp
    _self.align_pass0_with_whisper = web.align_pass0_with_whisper
    _self.render_debug_log = web.render_debug_log
    _self.get_s3_client = web.get_s3_client
    _self.save_job = web.save_job
    _self.load_job = web.load_job
    _self.invoke_lambda_async = web.invoke_lambda_async
    _self._run_pipeline_for_lesson = web._run_pipeline_for_lesson
    _self._pipeline_run_lock = web._pipeline_run_lock
    _self._is_pipeline_locked = web._is_pipeline_locked
    _self.ANALYSIS_TIMEOUT = web.ANALYSIS_TIMEOUT
