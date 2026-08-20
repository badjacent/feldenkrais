"""Versioned empirical calibration for canonical-summary cosine scores."""

from __future__ import annotations

import json
import hashlib
import random
from collections import Counter
from dataclasses import asdict, dataclass
from itertools import combinations
from pathlib import Path
from typing import Iterable

from .measures import EmbeddingECDF, LessonProfile, cosine_similarity


@dataclass(frozen=True)
class CalibrationArtifact:
    schema_version: str
    extraction_model: str
    embedding_method: str
    random_seed: int
    sampled_pairs: int
    cosine_values: tuple[float, ...]

    @property
    def version(self) -> str:
        values_hash = hashlib.sha256(
            json.dumps(self.cosine_values, separators=(",", ":")).encode()
        ).hexdigest()[:12]
        return (
            f"{self.schema_version}:{self.extraction_model}:"
            f"{self.embedding_method}:{values_hash}"
        )

    def ecdf(self) -> EmbeddingECDF:
        return EmbeddingECDF(self.cosine_values, self.version)

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2, sort_keys=True) + "\n")

    @classmethod
    def read(cls, path: Path) -> "CalibrationArtifact":
        data = json.loads(path.read_text())
        data["cosine_values"] = tuple(data["cosine_values"])
        artifact = cls(**data)
        if artifact.sampled_pairs != len(artifact.cosine_values):
            raise ValueError("calibration pair count does not match stored values")
        return artifact


def build_embedding_calibration(
    profiles: Iterable[LessonProfile],
    *,
    schema_version: str,
    extraction_model: str,
    embedding_method: str = "default",
    sample_size: int = 10_000,
    random_seed: int = 17,
) -> CalibrationArtifact:
    """Uniformly sample distinct, non-rendition ATM profile pairs."""
    if sample_size <= 0:
        raise ValueError("sample size must be positive")
    eligible = sorted(
        (profile for profile in profiles if profile.embedding),
        key=lambda profile: profile.document_id,
    )
    if len(eligible) < 2:
        raise ValueError("at least two embedded profiles are required")
    group_sizes = Counter(profile.lesson_key for profile in eligible)
    allowed_pair_count = len(eligible) * (len(eligible) - 1) // 2 - sum(
        size * (size - 1) // 2 for size in group_sizes.values()
    )
    target = min(sample_size, allowed_pair_count)
    if target == 0:
        raise ValueError("all embedded profiles belong to one rendition group")

    if allowed_pair_count <= sample_size or len(eligible) <= 200:
        pairs = [
            (left, right)
            for left, right in combinations(eligible, 2)
            if left.lesson_key != right.lesson_key
        ]
        random.Random(random_seed).shuffle(pairs)
        pairs = pairs[:target]
    else:
        rng = random.Random(random_seed)
        pair_indices: set[tuple[int, int]] = set()
        while len(pair_indices) < target:
            left_index, right_index = rng.sample(range(len(eligible)), 2)
            pair = tuple(sorted((left_index, right_index)))
            if eligible[pair[0]].lesson_key != eligible[pair[1]].lesson_key:
                pair_indices.add(pair)
        pairs = [(eligible[left], eligible[right]) for left, right in sorted(pair_indices)]

    values = []
    for left, right in pairs:
        value = cosine_similarity(left.embedding, right.embedding)
        if value is None:
            raise ValueError("calibration encountered incompatible embedding dimensions")
        values.append(value)
    return CalibrationArtifact(
        schema_version=schema_version,
        extraction_model=extraction_model,
        embedding_method=embedding_method,
        random_seed=random_seed,
        sampled_pairs=len(values),
        cosine_values=tuple(sorted(values)),
    )
