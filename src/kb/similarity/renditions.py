"""Rendition gate for the lesson-similarity system.

Renditions are detected before any similarity measure is evaluated.  The gate
is deliberately independent of pass0b/profile extraction so duplicate lessons
cannot leak into fixture results while later profile work is incomplete.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any, Iterable, Mapping, Sequence

RENDITION_DETECTION_VERSION = "rendition-v2"
SHINGLE_THRESHOLD = 0.65
RAW_COSINE_THRESHOLD = 0.985
DEFAULT_SHINGLE_SIZE = 7
ATM_ONLY_SOURCES = {"FELDENKRAIS_PROJECT", "FLOWING_BODY", "OPENATM"}

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)?")
_AY_RE = re.compile(
    r"\b(?:ay|alexander\s+yanai)(?:\s+lesson)?\s*[-:#]?\s*0*(\d{1,3})\b",
    re.IGNORECASE,
)
_ATM_BOOK_RE = re.compile(
    r"\b(?:awareness\s+through\s+movement|atm)\s+(?:book\s+)?lesson\s*[-:#]?\s*0*(\d{1,2})\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RenditionDocument:
    """Only the fields the pre-scoring gate is allowed to inspect."""

    document_id: str
    title: str
    metadata: Mapping[str, Any] = field(default_factory=dict)
    transcript: str = ""
    raw_embedding: Sequence[float] | None = None
    source_name: str | None = None


@dataclass(frozen=True)
class RenditionEvidence:
    left_id: str
    right_id: str
    reasons: tuple[str, ...]
    shingle_overlap: float | None = None
    raw_cosine: float | None = None
    canonical_identity: str | None = None


@dataclass(frozen=True)
class RenditionBuild:
    """Complete gate result, including unique keys for singleton lessons."""

    lesson_keys: Mapping[str, str]
    groups: Mapping[str, tuple[str, ...]]
    evidence: tuple[RenditionEvidence, ...]
    excluded: Mapping[str, str]
    detection_version: str = RENDITION_DETECTION_VERSION

    def renditions_for(self, document_id: str) -> tuple[str, ...]:
        lesson_key = self.lesson_keys.get(document_id)
        if lesson_key is None:
            return ()
        return tuple(member for member in self.groups[lesson_key] if member != document_id)

    def same_rendition(self, left_id: str, right_id: str) -> bool:
        left_key = self.lesson_keys.get(left_id)
        return left_key is not None and left_key == self.lesson_keys.get(right_id)


def _metadata_lesson_type(metadata: Mapping[str, Any]) -> str:
    direct = metadata.get("lesson_type")
    if direct:
        return str(direct).strip().upper()
    lesson_page = metadata.get("lesson_page")
    if isinstance(lesson_page, Mapping):
        extracted = lesson_page.get("extracted")
        if isinstance(extracted, Mapping) and extracted.get("lesson_type"):
            return str(extracted["lesson_type"]).strip().upper()
    return ""


def is_multi_lesson(metadata: Mapping[str, Any]) -> bool:
    """Fail closed when ingestion says a recording still needs splitting."""
    for key in ("multi_lesson", "multiple_lessons", "requires_split"):
        value = metadata.get(key)
        if value is True or (isinstance(value, str) and value.lower() == "true"):
            return True
    count = metadata.get("lesson_count")
    try:
        return count is not None and int(count) > 1
    except (TypeError, ValueError):
        return True


def eligibility_reason(document: RenditionDocument) -> str | None:
    explicitly_atm = _metadata_lesson_type(document.metadata) == "ATM"
    source_is_atm_only = document.source_name in ATM_ONLY_SOURCES
    if not explicitly_atm and not source_is_atm_only:
        return "not_atm"
    if is_multi_lesson(document.metadata):
        return "multi_lesson_requires_split"
    if not document.transcript.strip():
        return "missing_transcript"
    return None


def is_similarity_eligible(document: RenditionDocument) -> bool:
    return eligibility_reason(document) is None


def _flatten_identity_text(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for key, child in value.items():
            if key not in {"text", "transcript", "segments", "words"}:
                yield from _flatten_identity_text(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _flatten_identity_text(child)


def _normalize_explicit_identity(value: Any) -> str | None:
    if value is None:
        return None
    text = re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")
    return f"canonical:{text}" if text else None


def canonical_lesson_identity(document: RenditionDocument) -> str | None:
    """Extract an unambiguous shared AY/book identity from title and metadata."""
    for key in ("canonical_lesson_identity", "canonical_lesson_id"):
        explicit = _normalize_explicit_identity(document.metadata.get(key))
        if explicit:
            return explicit

    ay_number = document.metadata.get("ay_number")
    if ay_number is not None:
        try:
            return f"ay:{int(ay_number)}"
        except (TypeError, ValueError):
            pass

    texts = [document.title, *_flatten_identity_text(document.metadata)]
    ay_matches = {int(match) for text in texts for match in _AY_RE.findall(text)}
    if len(ay_matches) == 1:
        return f"ay:{ay_matches.pop()}"

    book_matches = {int(match) for text in texts for match in _ATM_BOOK_RE.findall(text)}
    if len(book_matches) == 1:
        return f"atm_book:{book_matches.pop()}"
    return None


def transcript_shingles(text: str, size: int = DEFAULT_SHINGLE_SIZE) -> frozenset[str]:
    tokens = _TOKEN_RE.findall(text.lower())
    if len(tokens) < size:
        return frozenset()
    return frozenset(" ".join(tokens[index : index + size]) for index in range(len(tokens) - size + 1))


def shingle_overlap(left: frozenset[str], right: frozenset[str]) -> float | None:
    if not left or not right:
        return None
    union_size = len(left | right)
    return len(left & right) / union_size if union_size else None


def cosine_similarity(left: Sequence[float] | None, right: Sequence[float] | None) -> float | None:
    if left is None or right is None or len(left) != len(right) or not left:
        return None
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return None
    return max(-1.0, min(1.0, dot / (left_norm * right_norm)))


class _DisjointSet:
    def __init__(self, ids: Iterable[str]):
        self.parent = {item: item for item in ids}

    def find(self, item: str) -> str:
        root = item
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[item] != item:
            item, self.parent[item] = self.parent[item], root
        return root

    def union(self, left: str, right: str) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root != right_root:
            self.parent[max(left_root, right_root)] = min(left_root, right_root)


def _new_lesson_key(member_ids: Sequence[str], identities: set[str]) -> str:
    if len(identities) == 1:
        return next(iter(identities))
    digest = hashlib.sha256("\0".join(sorted(member_ids)).encode()).hexdigest()[:20]
    prefix = "rendition" if len(member_ids) > 1 else "lesson"
    return f"{prefix}:{digest}"


def build_rendition_groups(
    documents: Iterable[RenditionDocument],
    *,
    existing_lesson_keys: Mapping[str, str] | None = None,
    shingle_threshold: float = SHINGLE_THRESHOLD,
    raw_cosine_threshold: float = RAW_COSINE_THRESHOLD,
    shingle_size: int = DEFAULT_SHINGLE_SIZE,
) -> RenditionBuild:
    """Apply all gates and build transitive rendition groups.

    Threshold comparisons are strict, matching the measure specification.
    Existing persisted rendition keys win when a group is rebuilt so adding a
    newly discovered rendition does not rename an established lesson family.
    """
    if not 0 <= shingle_threshold <= 1 or not -1 <= raw_cosine_threshold <= 1:
        raise ValueError("rendition thresholds are outside their valid ranges")

    docs = sorted(documents, key=lambda item: item.document_id)
    excluded = {
        document.document_id: reason
        for document in docs
        if (reason := eligibility_reason(document)) is not None
    }
    eligible = [document for document in docs if document.document_id not in excluded]
    identities = {document.document_id: canonical_lesson_identity(document) for document in eligible}
    shingles = {
        document.document_id: transcript_shingles(document.transcript, shingle_size)
        for document in eligible
    }
    union = _DisjointSet(document.document_id for document in eligible)
    evidence: list[RenditionEvidence] = []

    for left, right in combinations(eligible, 2):
        identity = identities[left.document_id]
        identity_match = identity is not None and identity == identities[right.document_id]
        overlap = shingle_overlap(shingles[left.document_id], shingles[right.document_id])
        raw_cosine = cosine_similarity(left.raw_embedding, right.raw_embedding)
        reasons = []
        if identity_match:
            reasons.append("canonical_identity")
        if overlap is not None and overlap > shingle_threshold:
            reasons.append("transcript_shingles")
        if raw_cosine is not None and raw_cosine > raw_cosine_threshold:
            reasons.append("raw_embedding")
        if not reasons:
            continue
        union.union(left.document_id, right.document_id)
        evidence.append(
            RenditionEvidence(
                left.document_id,
                right.document_id,
                tuple(reasons),
                overlap,
                raw_cosine,
                identity if identity_match else None,
            )
        )

    member_groups: dict[str, list[str]] = {}
    for document in eligible:
        member_groups.setdefault(union.find(document.document_id), []).append(document.document_id)

    persisted = existing_lesson_keys or {}
    lesson_keys: dict[str, str] = {}
    groups: dict[str, tuple[str, ...]] = {}
    for members in sorted(member_groups.values(), key=lambda values: tuple(sorted(values))):
        members = sorted(members)
        prior_keys = sorted({persisted[member] for member in members if member in persisted})
        group_identities = {identities[member] for member in members if identities[member] is not None}
        lesson_key = prior_keys[0] if prior_keys else _new_lesson_key(members, group_identities)
        groups[lesson_key] = tuple(members)
        lesson_keys.update({member: lesson_key for member in members})

    return RenditionBuild(
        lesson_keys=lesson_keys,
        groups=groups,
        evidence=tuple(evidence),
        excluded=excluded,
    )
