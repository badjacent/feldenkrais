"""Projection of existing pass0b artifacts into similarity profile v1."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from .measures import LessonProfile, TrademarkMechanism

PROFILE_SCHEMA_VERSION = "profile-v1"
ORDINAL_WEIGHTS = {"primary": 1.0, "secondary": 0.6, "present": 0.3}

REGION_TERMS = {
    "head_neck": ("head", "neck", "cervical", "skull"),
    "eyes": ("eye", "eyes", "gaze", "vision"),
    "jaw_mouth": ("jaw", "mouth", "tongue", "teeth", "palate", "lips"),
    "shoulders_scapulae": ("shoulder", "shoulders", "scapula", "scapulae", "shoulder blade"),
    "arms_hands": ("arm", "arms", "hand", "hands", "elbow", "wrist", "finger"),
    "ribs_chest": ("rib", "ribs", "chest", "sternum", "breastbone"),
    "spine": ("spine", "vertebra", "back"),
    "pelvis": ("pelvis", "pelvic", "sacrum", "sit bone"),
    "hips": ("hip", "hips", "hip joint"),
    "legs": ("leg", "legs", "knee", "knees", "thigh"),
    "feet": ("foot", "feet", "heel", "heels", "ankle"),
    "whole_body": ("whole body", "whole self", "entire body"),
}

POSITION_TERMS = {
    "supine": ("supine", "lying on the back", "lie on your back", "on the back"),
    "prone": ("prone", "lying on the stomach", "lie on your stomach", "on the belly"),
    "side_lying": ("side lying", "side-lying", "lying on the side", "lie on your side"),
    "sitting": ("sitting", "seated", "sit "),
    "kneeling": ("kneeling", "on all fours", "hands and knees"),
    "standing": ("standing", "stand ", "upright"),
    "transitional": ("transition", "come to", "roll to sit", "come up"),
}

THEME_TERMS = {
    "flexion": ("flexion", "flex", "curl"),
    "extension": ("extension", "extend"),
    "rotation": ("rotation", "rotate", "turn", "twist"),
    "side_bending": ("side bend", "side-bend", "lateral flex"),
    "rolling": ("rolling", "roll", "rocking", "rock"),
    "translation_sliding": ("slide", "sliding", "shift"),
    "reaching_lengthening": ("reach", "lengthen", "lengthening"),
    "folding": ("fold", "folding"),
    "weight_shift": ("weight shift", "shift your weight"),
    "support_transfer": ("transfer support", "support transfer", "press into"),
    "arching_rounding": ("arch", "rounding", "round your"),
}

SENSORY_TERMS = {
    "eyes": ("eye", "eyes", "gaze", "look"),
    "breath": ("breath", "breathe", "breathing"),
    "contact_floor": ("floor", "contact", "pressure", "support"),
    "felt_length": ("length", "longer", "shorter", "distance"),
    "vestibular": ("balance", "orientation", "dizzy", "horizon"),
}


@dataclass(frozen=True)
class ProjectedProfile:
    profile: LessonProfile
    schema_version: str
    extraction_model: str
    provenance: Mapping[str, Any]


def profile_to_payload(projected: ProjectedProfile) -> dict[str, Any]:
    """Return a stable JSON representation (tuple-key maps need encoding)."""
    profile = asdict(projected.profile)
    # Canonical-summary embeddings remain in pgvector; do not duplicate them
    # in the JSONB profile or derived artifact metadata.
    profile.pop("embedding")
    profile["diffs"] = [
        {"parts": list(parts), "weight": weight}
        for parts, weight in sorted(projected.profile.diffs.items())
    ]
    return {
        "schema_version": projected.schema_version,
        "extraction_model": projected.extraction_model,
        "provenance": dict(projected.provenance),
        "profile": profile,
    }


def profile_from_payload(payload: Mapping[str, Any], *, embedding=None) -> ProjectedProfile:
    """Rehydrate a stored profile after joining its pgvector embedding."""
    values = dict(payload["profile"])
    values["diffs"] = {
        tuple(item["parts"]): float(item["weight"])
        for item in values.get("diffs", [])
    }
    values["marks"] = tuple(TrademarkMechanism(**item) for item in values.get("marks", []))
    values["embedding"] = embedding
    return ProjectedProfile(
        profile=LessonProfile(**values),
        schema_version=str(payload["schema_version"]),
        extraction_model=str(payload["extraction_model"]),
        provenance=dict(payload.get("provenance", {})),
    )


def profile_content_hash(projected: ProjectedProfile) -> str:
    encoded = json.dumps(profile_to_payload(projected), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _steps(data: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    if not data:
        return []
    value = data.get("steps") or data.get("enriched_steps") or []
    return [step for step in value if isinstance(step, Mapping)]


def _text_values(values: Iterable[Any]) -> str:
    return " ".join(str(value) for value in values if value and str(value).lower() != "none").lower()


def _count_terms(texts: Iterable[str], vocabulary: Mapping[str, Sequence[str]]) -> Counter:
    counts: Counter = Counter()
    for text in texts:
        lowered = str(text).lower()
        for label, terms in vocabulary.items():
            if any(re.search(rf"\b{re.escape(term)}", lowered) for term in terms):
                counts[label] += 1
    return counts


def _ordinalize(counts: Mapping[str, int]) -> dict[str, float]:
    if not counts:
        return {}
    maximum = max(counts.values())
    result = {}
    for key, count in counts.items():
        ratio = count / maximum
        level = "primary" if ratio >= 0.75 else "secondary" if ratio >= 0.4 else "present"
        result[key] = ORDINAL_WEIGHTS[level]
    return result


def _normalize_concepts(concepts: Mapping[str, Any] | None) -> tuple[Counter, Counter, Counter]:
    regions: Counter = Counter()
    themes: Counter = Counter()
    positions: Counter = Counter()
    for concept in (concepts or {}).get("concepts", []):
        if not isinstance(concept, Mapping):
            continue
        category = str(concept.get("category", "")).lower()
        value = str(concept.get("value", ""))
        if category == "body_region":
            regions.update(_count_terms([value], REGION_TERMS))
        elif category in {"movement_pattern", "theme"}:
            themes.update(_count_terms([value], THEME_TERMS))
        elif category == "position":
            positions.update(_count_terms([value], POSITION_TERMS))
    return regions, themes, positions


def _step_weight(step: Mapping[str, Any]) -> float | None:
    duration = step.get("duration_seconds")
    if duration is not None:
        try:
            return max(0.0, float(duration))
        except (TypeError, ValueError):
            return None
    start, end = step.get("time_start"), step.get("time_end")
    if start is not None and end is not None:
        try:
            return max(0.0, float(end) - float(start))
        except (TypeError, ValueError):
            return None
    return None


def _position_distribution(
    spatial: Mapping[str, Any] | None,
    decompose: Mapping[str, Any] | None,
    concept_positions: Counter,
) -> tuple[dict[str, float], str]:
    weighted: Counter = Counter()
    for step in _steps(spatial):
        weight = _step_weight(step)
        if weight is None or weight <= 0:
            continue
        labels = _count_terms([str(step.get("position", ""))], POSITION_TERMS)
        if len(labels) == 1:
            weighted[next(iter(labels))] += weight
    if weighted:
        total = sum(weighted.values())
        return ({key: value / total for key, value in weighted.items()}, "spatial_step_duration")

    # No duration means no fabricated distribution. Fall back to fixed ordinal
    # evidence from explicit position tags or decomposition starting positions.
    fallback = Counter(concept_positions)
    starts = [step.get("starting_position", "") for step in _steps(decompose)]
    fallback.update(_count_terms(starts, POSITION_TERMS))
    groups = (decompose or {}).get("groups", []) if decompose else []
    fallback.update(
        _count_terms(
            [group.get("starting_position", "") for group in groups if isinstance(group, Mapping)],
            POSITION_TERMS,
        )
    )
    return (_ordinalize(fallback), "position_tags_fallback" if fallback else "missing")


def _route_category(route: str) -> str | None:
    parts = [part.strip() for part in re.split(r"(?:→|->)", route) if part.strip()]
    if len(parts) < 2:
        return None
    central = {"ribs_chest", "spine", "pelvis", "hips", "whole_body"}
    distal = {"arms_hands", "legs", "feet", "head_neck"}
    start_labels = set(_count_terms([parts[0]], REGION_TERMS))
    end_labels = set(_count_terms([parts[-1]], REGION_TERMS))
    if len(parts) >= 4 and (start_labels & distal) and (end_labels & distal):
        return "whole_chain"
    if (start_labels & distal) and (end_labels & central):
        return "distal_to_proximal"
    if (start_labels & central) and (end_labels & distal):
        return "proximal_to_distal"
    if (start_labels & central) and (end_labels & central):
        return "primarily_proximal"
    return None


def _prox_dist(somatic_steps: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    counts = Counter(
        category
        for step in somatic_steps
        if (category := _route_category(str(step.get("transmission_route", ""))))
    )
    total = sum(counts.values())
    return {key: value / total for key, value in counts.items()} if total else {}


def _differentiations(somatic_steps: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str], float]:
    counts: Counter = Counter()
    for step in somatic_steps:
        labels = sorted(_count_terms([str(step.get("differentiation", ""))], REGION_TERMS))
        if len(labels) == 2:
            counts[tuple(labels)] += 1
    return {tuple(key.split("|")): value for key, value in _ordinalize({"|".join(pair): count for pair, count in counts.items()}).items()}


def _scalar_features(
    themes: Mapping[str, float],
    somatic_steps: Sequence[Mapping[str, Any]],
    spatial_steps: Sequence[Mapping[str, Any]],
    all_text: str,
) -> dict[str, float]:
    result = {}
    if "rolling" in themes:
        result["rolling"] = themes["rolling"]
    relevant_spatial = [step for step in spatial_steps if not step.get("is_rest")]
    if relevant_spatial:
        result["transition_emphasis"] = sum(bool(step.get("is_transition")) for step in relevant_spatial) / len(relevant_spatial)
    relevant_somatic = [step for step in somatic_steps if step.get("relevance", True)]
    if relevant_somatic:
        ground = sum(str(step.get("ground_push", "")).lower() not in {"", "none"} for step in relevant_somatic)
        result["support_level"] = min(1.0, ground / len(relevant_somatic))
    words = max(1, len(all_text.split()))
    imagined = len(re.findall(r"\b(?:imagine|imagined|imagining|visualize|pretend)\b", all_text))
    result["imagination_ratio"] = min(1.0, imagined / max(1, words * 0.02))
    vigorous = len(re.findall(r"\b(?:large|larger|quick|vigorous|strong|forceful)\b", all_text))
    quiet = len(re.findall(r"\b(?:small|smaller|tiny|gentle|quiet|delicate|minimal)\b", all_text))
    if vigorous or quiet:
        result["movement_scale"] = vigorous / (vigorous + quiet)
        result["vigor"] = vigorous / (vigorous + quiet)
    reversible = len(re.findall(r"\b(?:return|reverse|reversible|back and forth|easily back)\b", all_text))
    if reversible:
        result["reversibility"] = min(1.0, reversible / max(1, words * 0.01))
    return result


def project_existing_analysis(
    *,
    document_id: str,
    lesson_key: str,
    concepts: Mapping[str, Any] | None = None,
    decompose: Mapping[str, Any] | None = None,
    somatic: Mapping[str, Any] | None = None,
    language: Mapping[str, Any] | None = None,
    spatial: Mapping[str, Any] | None = None,
    embedding: Sequence[float] | None = None,
    duration_seconds: int | None = None,
    marks: Sequence[TrademarkMechanism] = (),
    architecture: Mapping[str, Any] | None = None,
    extraction_model: str = "pass0b-existing",
) -> ProjectedProfile:
    concept_regions, concept_themes, concept_positions = _normalize_concepts(concepts)
    somatic_steps = _steps(somatic)
    spatial_steps = _steps(spatial)
    step_texts = [
        _text_values(
            step.get(key)
            for key in (
                "mechanical_directive",
                "clarified",
                "sensory_inquiry",
                "attentional_cue",
                "differentiation",
            )
        )
        for step in somatic_steps
    ]
    region_counts = Counter(concept_regions)
    region_counts.update(_count_terms(step_texts, REGION_TERMS))
    theme_counts = Counter(concept_themes)
    theme_counts.update(_count_terms(step_texts, THEME_TERMS))
    regions = _ordinalize(region_counts)
    themes = _ordinalize(theme_counts)
    positions, position_source = _position_distribution(spatial, decompose, concept_positions)

    language_steps = _steps(language)
    attention_texts = [
        _text_values(step.get(key) for key in ("attention_cues", "image_language"))
        for step in language_steps
    ] + step_texts
    sensory = _ordinalize(_count_terms(attention_texts, SENSORY_TERMS))
    all_text = " ".join(step_texts + attention_texts)
    arch = architecture or {}
    profile = LessonProfile(
        document_id=document_id,
        lesson_key=lesson_key,
        regions=regions,
        positions=positions,
        themes=themes,
        sensory=sensory,
        prox_dist=_prox_dist(somatic_steps),
        diffs=_differentiations(somatic_steps),
        scalars=_scalar_features(themes, somatic_steps, spatial_steps, all_text),
        architecture_type=arch.get("type"),
        side_compare=arch.get("side_compare"),
        act_vs_relationship=arch.get("act_vs_relationship"),
        marks=tuple(marks),
        embedding=embedding,
        duration_seconds=duration_seconds,
    )
    return ProjectedProfile(
        profile=profile,
        schema_version=PROFILE_SCHEMA_VERSION,
        extraction_model=extraction_model,
        provenance={
            "regions": "concept_tags+somatic_steps" if regions else "missing",
            "themes": "concept_tags+somatic_steps" if themes else "missing",
            "positions": position_source,
            "sensory": "language+somatic_steps" if sensory else "missing",
            "prox_dist": "somatic.transmission_route" if profile.prox_dist else "missing",
            "diffs": "somatic.differentiation" if profile.diffs else "missing",
            "architecture": "dedicated_extraction" if architecture else "missing",
            "marks": "dedicated_extraction" if marks else "missing",
        },
    )
