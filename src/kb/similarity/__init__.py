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

__all__ = [
    "RENDITION_DETECTION_VERSION",
    "RenditionBuild",
    "RenditionDocument",
    "RenditionEvidence",
    "build_rendition_groups",
    "canonical_lesson_identity",
    "is_similarity_eligible",
    "shingle_overlap",
]
