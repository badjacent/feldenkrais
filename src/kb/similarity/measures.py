"""Inspectable v1 lesson-similarity measures.

All public scoring functions apply the rendition gate before arithmetic.  The
module contains no extraction or model calls; it consumes versioned profiles
projected from existing analysis artifacts.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

DEFAULT_STRUCTURED_WEIGHTS = {
    "regions": 0.18,
    "themes": 0.16,
    "positions": 0.12,
    "diffs": 0.14,
    "prox_dist": 0.10,
    "sensory": 0.08,
    "scalars": 0.10,
    "arch": 0.12,
}

ARCHITECTURE_AFFINITY = {
    frozenset(("single_motif", "variations_around_motif")): 0.6,
    frozenset(("parts_then_integration", "functional_progression")): 0.4,
    frozenset(("multiple_explorations_converging", "parts_then_integration")): 0.5,
}


@dataclass(frozen=True)
class TrademarkMechanism:
    mechanism_id: str
    strength: float
    mode: str

    def __post_init__(self):
        if not 0 <= self.strength <= 1:
            raise ValueError("trademark strength must be in [0,1]")
        if self.mode not in {"taught", "referenced"}:
            raise ValueError("trademark mode must be taught or referenced")


@dataclass(frozen=True)
class LessonProfile:
    document_id: str
    lesson_key: str
    regions: Mapping[str, float] = field(default_factory=dict)
    positions: Mapping[str, float] = field(default_factory=dict)
    themes: Mapping[str, float] = field(default_factory=dict)
    sensory: Mapping[str, float] = field(default_factory=dict)
    prox_dist: Mapping[str, float] = field(default_factory=dict)
    diffs: Mapping[tuple[str, str], float] = field(default_factory=dict)
    scalars: Mapping[str, float] = field(default_factory=dict)
    architecture_type: str | None = None
    side_compare: bool | None = None
    act_vs_relationship: bool | None = None
    marks: tuple[TrademarkMechanism, ...] = ()
    embedding: Sequence[float] | None = None
    duration_seconds: int | None = None
    language: str | None = None


@dataclass(frozen=True)
class FieldScore:
    score: float
    weight: float
    contribution: float
    shared: tuple[tuple[str, float], ...] = ()
    divergences: tuple[tuple[str, float], ...] = ()


@dataclass(frozen=True)
class SimilarityResult:
    left_id: str
    right_id: str
    similarity: float
    base: float
    structured: float | None
    embedding: float | None
    duration: float | None
    language: float | None
    fields: Mapping[str, FieldScore]
    missing_fields: tuple[str, ...]
    trademark_mechanism: str | None = None
    trademark_strength: float = 0.0
    trademark_lift: float = 0.0


@dataclass(frozen=True)
class EmbeddingECDF:
    values: tuple[float, ...]
    version: str

    def __post_init__(self):
        if not self.values:
            raise ValueError("embedding ECDF cannot be empty")
        if tuple(sorted(self.values)) != self.values:
            raise ValueError("embedding ECDF values must be sorted")

    def normalize(self, cosine: float) -> float:
        return bisect.bisect_right(self.values, cosine) / len(self.values)


def _validate_weights(values: Mapping[str, float]) -> None:
    if any(not 0 <= value <= 1 for value in values.values()):
        raise ValueError("profile weights must be in [0,1]")


def ruzicka(left: Mapping[str, float], right: Mapping[str, float]) -> tuple[float, tuple, tuple] | None:
    """Generalized Jaccard plus its deterministic explanation terms."""
    if not left or not right:
        return None
    _validate_weights(left)
    _validate_weights(right)
    keys = set(left) | set(right)
    minima = {key: min(left.get(key, 0.0), right.get(key, 0.0)) for key in keys}
    maxima = {key: max(left.get(key, 0.0), right.get(key, 0.0)) for key in keys}
    denominator = sum(maxima.values())
    score = sum(minima.values()) / denominator if denominator else 0.0
    shared = tuple(sorted(((key, value) for key, value in minima.items() if value), key=lambda x: (-x[1], x[0]))[:3])
    divergences = tuple(
        sorted(
            ((key, abs(left.get(key, 0.0) - right.get(key, 0.0))) for key in keys),
            key=lambda x: (-x[1], x[0]),
        )[:3]
    )
    return score, shared, divergences


def _pair_keys(values: Mapping[tuple[str, str], float]) -> dict[str, float]:
    return {" ↔ ".join(sorted(parts)): value for parts, value in values.items()}


def scalar_similarity(left: Mapping[str, float], right: Mapping[str, float]) -> tuple[float, tuple, tuple] | None:
    common = sorted(set(left) & set(right))
    if not common:
        return None
    _validate_weights({key: left[key] for key in common})
    _validate_weights({key: right[key] for key in common})
    per_scalar = {key: 1 - abs(left[key] - right[key]) for key in common}
    divergences = tuple(sorted(((key, 1 - value) for key, value in per_scalar.items()), key=lambda x: (-x[1], x[0]))[:3])
    shared = tuple(sorted(per_scalar.items(), key=lambda x: (-x[1], x[0]))[:3])
    return sum(per_scalar.values()) / len(per_scalar), shared, divergences


def architecture_similarity(left: LessonProfile, right: LessonProfile) -> tuple[float, tuple, tuple] | None:
    if not left.architecture_type or not right.architecture_type:
        return None
    if left.architecture_type == right.architecture_type:
        type_score = 1.0
    else:
        key = frozenset((left.architecture_type, right.architecture_type))
        type_score = ARCHITECTURE_AFFINITY.get(key, 0.0)
    type_label = f"{left.architecture_type} ↔ {right.architecture_type}"
    terms = [("type", 0.7, type_score, type_label)]
    if left.side_compare is not None and right.side_compare is not None:
        terms.append(("side_compare", 0.15, float(left.side_compare == right.side_compare), "side_compare"))
    if left.act_vs_relationship is not None and right.act_vs_relationship is not None:
        terms.append(
            ("act_vs_relationship", 0.15, float(left.act_vs_relationship == right.act_vs_relationship), "act_vs_relationship")
        )
    denominator = sum(weight for _, weight, _, _ in terms)
    score = sum(weight * value for _, weight, value, _ in terms) / denominator
    shared = tuple((label, value) for _, _, value, label in terms if value > 0)
    divergences = tuple((label, 1 - value) for _, _, value, label in terms if value < 1)
    return score, shared[:3], divergences[:3]


def cosine_similarity(left: Sequence[float] | None, right: Sequence[float] | None) -> float | None:
    if left is None or right is None or not left or len(left) != len(right):
        return None
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norms = math.sqrt(sum(value * value for value in left)) * math.sqrt(
        sum(value * value for value in right)
    )
    return max(-1.0, min(1.0, dot / norms)) if norms else None


def duration_similarity(left_seconds: int | None, right_seconds: int | None) -> float | None:
    if left_seconds is None or right_seconds is None:
        return None
    if left_seconds < 0 or right_seconds < 0:
        raise ValueError("duration cannot be negative")
    return math.exp(-abs(left_seconds - right_seconds) / 1800)


def normalize_language(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().casefold().replace("_", "-")
    aliases = {
        "english": "en",
        "spanish": "es",
        "español": "es",
        "french": "fr",
        "français": "fr",
        "german": "de",
        "deutsch": "de",
        "hebrew": "he",
        "japanese": "ja",
        "italian": "it",
        "portuguese": "pt",
    }
    normalized = aliases.get(normalized, normalized).split("-", 1)[0]
    return None if normalized in {"", "none", "null", "und", "unknown"} else normalized


def language_similarity(left: str | None, right: str | None) -> float | None:
    left_code = normalize_language(left)
    right_code = normalize_language(right)
    if left_code is None or right_code is None:
        return None
    return float(left_code == right_code)


def trademark_match(left: LessonProfile, right: LessonProfile) -> tuple[float, str | None]:
    mode_multiplier = {
        ("taught", "taught"): 1.0,
        ("referenced", "taught"): 0.3,
        ("taught", "referenced"): 0.3,
        ("referenced", "referenced"): 0.0,
    }
    right_by_id = {mark.mechanism_id: mark for mark in right.marks}
    matches = []
    for left_mark in left.marks:
        right_mark = right_by_id.get(left_mark.mechanism_id)
        if right_mark is None:
            continue
        strength = mode_multiplier[(left_mark.mode, right_mark.mode)] * min(
            left_mark.strength, right_mark.strength
        )
        matches.append((strength, left_mark.mechanism_id))
    return max(matches, default=(0.0, None), key=lambda item: (item[0], item[1] or ""))


def _field_measures(left: LessonProfile, right: LessonProfile) -> tuple[dict[str, tuple], list[str]]:
    raw = {
        "regions": ruzicka(left.regions, right.regions),
        "themes": ruzicka(left.themes, right.themes),
        "positions": ruzicka(left.positions, right.positions),
        "diffs": ruzicka(_pair_keys(left.diffs), _pair_keys(right.diffs)),
        "prox_dist": ruzicka(left.prox_dist, right.prox_dist),
        "sensory": ruzicka(left.sensory, right.sensory),
        "scalars": scalar_similarity(left.scalars, right.scalars),
        "arch": architecture_similarity(left, right),
    }
    return ({key: value for key, value in raw.items() if value is not None}, [key for key, value in raw.items() if value is None])


def score_pair(
    left: LessonProfile,
    right: LessonProfile,
    *,
    embedding_ecdf: EmbeddingECDF,
    structured_weights: Mapping[str, float] = DEFAULT_STRUCTURED_WEIGHTS,
    trademark_lambda: float = 0.5,
) -> SimilarityResult | None:
    """Score a pair, returning None when the rendition gate excludes it."""
    if left.document_id == right.document_id or left.lesson_key == right.lesson_key:
        return None
    if not 0 <= trademark_lambda <= 1:
        raise ValueError("trademark lambda must be in [0,1]")

    raw_fields, missing = _field_measures(left, right)
    participating_weight = sum(structured_weights[name] for name in raw_fields)
    structured = (
        sum(structured_weights[name] * values[0] for name, values in raw_fields.items())
        / participating_weight
        if participating_weight
        else None
    )
    fields = {
        name: FieldScore(
            score=values[0],
            weight=structured_weights[name] / participating_weight,
            contribution=(structured_weights[name] / participating_weight) * values[0],
            shared=values[1],
            divergences=values[2],
        )
        for name, values in raw_fields.items()
    }

    raw_cosine = cosine_similarity(left.embedding, right.embedding)
    embedding = embedding_ecdf.normalize(raw_cosine) if raw_cosine is not None else None
    duration = duration_similarity(left.duration_seconds, right.duration_seconds)
    language = language_similarity(left.language, right.language)
    blend_terms = [
        (0.52, structured),
        (0.38, embedding),
        (0.05, duration),
        (0.05, language),
    ]
    present_terms = [(weight, value) for weight, value in blend_terms if value is not None]
    if not present_terms:
        raise ValueError("pair has no comparable similarity fields")
    blend_weight = sum(weight for weight, _ in present_terms)
    base = sum(weight * value for weight, value in present_terms) / blend_weight
    match_strength, mechanism = trademark_match(left, right)
    lift = trademark_lambda * match_strength * (1 - base)
    similarity = base + lift
    return SimilarityResult(
        left_id=left.document_id,
        right_id=right.document_id,
        similarity=similarity,
        base=base,
        structured=structured,
        embedding=embedding,
        duration=duration,
        language=language,
        fields=fields,
        missing_fields=tuple(missing),
        trademark_mechanism=mechanism if match_strength else None,
        trademark_strength=match_strength,
        trademark_lift=lift,
    )


def similar_lessons(
    query: LessonProfile,
    candidates: Iterable[LessonProfile],
    *,
    embedding_ecdf: EmbeddingECDF,
    limit: int = 5,
) -> list[SimilarityResult]:
    scored = [
        result
        for candidate in candidates
        if (result := score_pair(query, candidate, embedding_ecdf=embedding_ecdf)) is not None
    ]
    return sorted(scored, key=lambda item: (-item.similarity, item.right_id))[:limit]


def is_profile_complete(profile: LessonProfile) -> bool:
    return bool(
        profile.regions
        and profile.positions
        and profile.themes
        and profile.sensory
        and profile.prox_dist
        and profile.diffs
        and profile.scalars
        and profile.architecture_type
        and profile.embedding
    )


def different_lessons(
    query: LessonProfile,
    candidates: Iterable[LessonProfile],
    *,
    embedding_ecdf: EmbeddingECDF,
    limit: int = 5,
) -> list[SimilarityResult]:
    scored = [
        result
        for candidate in candidates
        if is_profile_complete(candidate)
        if (result := score_pair(query, candidate, embedding_ecdf=embedding_ecdf)) is not None
    ]
    return sorted(scored, key=lambda item: (item.similarity, item.right_id))[:limit]


def contrastive_lessons(
    query: LessonProfile,
    candidates: Iterable[LessonProfile],
    *,
    hold_fields: Iterable[str],
    vary_fields: Iterable[str],
    embedding_ecdf: EmbeddingECDF,
    limit: int = 5,
) -> list[tuple[float, SimilarityResult]]:
    hold = tuple(hold_fields)
    vary = tuple(vary_fields)
    unknown = (set(hold) | set(vary)) - set(DEFAULT_STRUCTURED_WEIGHTS)
    if unknown:
        raise ValueError(f"unknown contrastive fields: {sorted(unknown)}")
    ranked = []
    for candidate in candidates:
        result = score_pair(query, candidate, embedding_ecdf=embedding_ecdf)
        if result is None:
            continue
        hold_values = [result.fields[name].score for name in hold if name in result.fields]
        vary_values = [result.fields[name].score for name in vary if name in result.fields]
        if not hold_values or not vary_values:
            continue
        score = sum(hold_values) / len(hold_values) - sum(vary_values) / len(vary_values) + 0.05 * result.base
        ranked.append((score, result))
    return sorted(ranked, key=lambda item: (-item[0], item[1].right_id))[:limit]


def diverse_lessons(
    query: LessonProfile,
    candidates: Iterable[LessonProfile],
    *,
    embedding_ecdf: EmbeddingECDF,
    limit: int = 5,
) -> list[SimilarityResult]:
    """MMR selection: 0.7 query similarity minus 0.3 redundancy."""
    pool = {
        candidate.document_id: (candidate, result)
        for candidate in candidates
        if (result := score_pair(query, candidate, embedding_ecdf=embedding_ecdf)) is not None
    }
    selected: list[tuple[LessonProfile, SimilarityResult]] = []
    while pool and len(selected) < limit:
        ranked = []
        for candidate, query_result in pool.values():
            redundancy = 0.0
            for chosen, _ in selected:
                pair = score_pair(candidate, chosen, embedding_ecdf=embedding_ecdf)
                # Same-rendition alternatives are never allowed in one result set.
                if pair is None:
                    redundancy = 1.0
                    break
                redundancy = max(redundancy, pair.similarity)
            mmr = 0.7 * query_result.similarity - 0.3 * redundancy
            ranked.append((mmr, candidate.document_id, candidate, query_result))
        _, document_id, candidate, result = max(ranked, key=lambda item: (item[0], item[1]))
        selected.append((candidate, result))
        pool = {
            candidate_id: value
            for candidate_id, value in pool.items()
            if value[0].lesson_key != candidate.lesson_key
        }
    return [result for _, result in selected]


def explain(result: SimilarityResult) -> dict:
    """Deterministic explanation payload; prose is a presentation concern."""
    return {
        "similarity": result.similarity,
        "base": result.base,
        "fields": {
            name: {
                "similarity": field.score,
                "weight": field.weight,
                "contribution": field.contribution,
                "shared": list(field.shared),
                "divergences": list(field.divergences),
            }
            for name, field in result.fields.items()
        },
        "embedding": result.embedding,
        "duration": result.duration,
        "language": result.language,
        "trademark": (
            {
                "mechanism_id": result.trademark_mechanism,
                "strength": result.trademark_strength,
                "lift": result.trademark_lift,
            }
            if result.trademark_mechanism
            else None
        ),
        "missing_fields": list(result.missing_fields),
    }
