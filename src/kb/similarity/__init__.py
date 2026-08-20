"""Interpretable lesson similarity primitives."""

from .renditions import (
    RENDITION_DETECTION_VERSION,
    RenditionBuild,
    RenditionDocument,
    RenditionEvidence,
    build_rendition_groups,
    canonical_lesson_identity,
    is_similarity_eligible,
    shingle_overlap,
)
from .measures import (
    EmbeddingECDF,
    LessonProfile,
    SimilarityResult,
    TrademarkMechanism,
    contrastive_lessons,
    diverse_lessons,
    different_lessons,
    explain,
    score_pair,
    similar_lessons,
)
from .calibration import CalibrationArtifact, build_embedding_calibration
from .profiles import (
    ORDINAL_WEIGHTS,
    PROFILE_SCHEMA_VERSION,
    ProjectedProfile,
    profile_content_hash,
    profile_from_payload,
    profile_to_payload,
    project_existing_analysis,
)
from .matrix import SimilarityMatrix, build_similarity_matrix

__all__ = [
    "RENDITION_DETECTION_VERSION",
    "RenditionBuild",
    "RenditionDocument",
    "RenditionEvidence",
    "build_rendition_groups",
    "canonical_lesson_identity",
    "is_similarity_eligible",
    "shingle_overlap",
    "EmbeddingECDF",
    "LessonProfile",
    "SimilarityResult",
    "TrademarkMechanism",
    "contrastive_lessons",
    "diverse_lessons",
    "different_lessons",
    "explain",
    "score_pair",
    "similar_lessons",
    "CalibrationArtifact",
    "build_embedding_calibration",
    "ORDINAL_WEIGHTS",
    "PROFILE_SCHEMA_VERSION",
    "ProjectedProfile",
    "profile_content_hash",
    "profile_from_payload",
    "profile_to_payload",
    "project_existing_analysis",
    "SimilarityMatrix",
    "build_similarity_matrix",
]
