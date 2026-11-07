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
):
    """PostgreSQL database wrapper for the knowledge base.

    Assembled from domain-specific mixins. All methods are available
    on this single class for backward compatibility.
    """
    pass


__all__ = ["Database", "SCHEMA", "INDEXES", "SEED_DATA"]

# Deployment-selected mixins provide optional domain-specific database methods.
# These module names come exclusively from trusted process configuration.
import importlib
import os

for _name in filter(None, os.environ.get("KB_DATABASE_MIXINS", "").split(",")):
    _module, _attribute = _name.strip().split(":", 1)
    _mixin = getattr(importlib.import_module(_module), _attribute)
    Database = type("Database", (_mixin, Database), {"__module__": __name__})
