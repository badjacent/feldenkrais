"""Database package — assembles the Database class from domain mixins.

The Database class is the single public API. It inherits from DatabaseCore
(connection pool, cursor, schema) and all domain mixins.
"""

from .core import DatabaseCore, SCHEMA, INDEXES, SEED_DATA
from .documents import DocumentsMixin
from .transcription import TranscriptionMixin
from .cache import CacheMixin
from .auth import AuthMixin
from .embeddings import EmbeddingsMixin
from .pipeline_state import PipelineStateMixin
from .analysis_profiles import AnalysisProfilesMixin
from .lesson_groups import LessonGroupsMixin
from .dashboard import DashboardMixin
from .logging import LoggingMixin
from .youtube import YouTubeMixin
from .fm_lessons import FMLessonsMixin
from .lesson_metadata import LessonMetadataMixin
from .analysis import AnalysisMixin
from .locks import LocksMixin
from .external_lessons import ExternalLessonsMixin
from .lesson_views import LessonViewsMixin
from .renditions import RenditionsMixin
from .similarity_profiles import SimilarityProfilesMixin
from .nyc import NycAdminMixin


class Database(
    DatabaseCore,
    DocumentsMixin,
    TranscriptionMixin,
    CacheMixin,
    AuthMixin,
    EmbeddingsMixin,
    PipelineStateMixin,
    AnalysisProfilesMixin,
    LessonGroupsMixin,
    DashboardMixin,
    LoggingMixin,
    YouTubeMixin,
    FMLessonsMixin,
    LessonMetadataMixin,
    AnalysisMixin,
    LocksMixin,
    ExternalLessonsMixin,
    LessonViewsMixin,
    RenditionsMixin,
    SimilarityProfilesMixin,
    NycAdminMixin,
):
    """PostgreSQL database wrapper for the knowledge base.

    Assembled from domain-specific mixins. All methods are available
    on this single class for backward compatibility.
    """
    pass


__all__ = ["Database", "SCHEMA", "INDEXES", "SEED_DATA"]
