"""Versioned float16 all-pairs similarity matrix artifact."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np

from .measures import EmbeddingECDF, LessonProfile, score_pair


@dataclass(frozen=True)
class SimilarityMatrix:
    document_ids: tuple[str, ...]
    lesson_keys: tuple[str, ...]
    content_hashes: tuple[str, ...]
    values: np.ndarray
    schema_version: str
    extraction_model: str
    weight_set: str
    ecdf_version: str

    def __post_init__(self):
        size = len(self.document_ids)
        if self.values.shape != (size, size):
            raise ValueError("similarity matrix shape does not match documents")
        if len(self.lesson_keys) != size or len(self.content_hashes) != size:
            raise ValueError("similarity matrix metadata length mismatch")

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        np.save(directory / "matrix.npy", self.values.astype(np.float16), allow_pickle=False)
        metadata = {
            "schema_version": self.schema_version,
            "extraction_model": self.extraction_model,
            "weight_set": self.weight_set,
            "ecdf_version": self.ecdf_version,
            "document_ids": list(self.document_ids),
            "lesson_keys": list(self.lesson_keys),
            "content_hashes": list(self.content_hashes),
            "dtype": "float16",
            "shape": list(self.values.shape),
        }
        (directory / "manifest.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n"
        )

    @classmethod
    def load(cls, directory: Path) -> "SimilarityMatrix":
        metadata = json.loads((directory / "manifest.json").read_text())
        values = np.load(directory / "matrix.npy", allow_pickle=False)
        if metadata.get("dtype") != "float16" or values.dtype != np.float16:
            raise ValueError("similarity matrix must be float16")
        if list(values.shape) != metadata.get("shape"):
            raise ValueError("matrix shape does not match manifest")
        return cls(
            document_ids=tuple(metadata["document_ids"]),
            lesson_keys=tuple(metadata["lesson_keys"]),
            content_hashes=tuple(metadata["content_hashes"]),
            values=values,
            schema_version=metadata["schema_version"],
            extraction_model=metadata["extraction_model"],
            weight_set=metadata["weight_set"],
            ecdf_version=metadata["ecdf_version"],
        )


def build_similarity_matrix(
    profiles: Sequence[LessonProfile],
    *,
    content_hashes: Mapping[str, str],
    embedding_ecdf: EmbeddingECDF,
    schema_version: str,
    extraction_model: str,
    weight_set: str,
    previous: SimilarityMatrix | None = None,
) -> tuple[SimilarityMatrix, int]:
    """Build a full matrix, reusing unchanged pairs from a compatible artifact."""
    ordered = sorted(profiles, key=lambda item: item.document_id)
    document_ids = tuple(item.document_id for item in ordered)
    lesson_keys = tuple(item.lesson_key for item in ordered)
    hashes = tuple(content_hashes[item.document_id] for item in ordered)
    size = len(ordered)
    values = np.full((size, size), np.nan, dtype=np.float16)
    reusable = (
        previous is not None
        and previous.schema_version == schema_version
        and previous.extraction_model == extraction_model
        and previous.weight_set == weight_set
        and previous.ecdf_version == embedding_ecdf.version
    )
    old_index = {document_id: index for index, document_id in enumerate(previous.document_ids)} if reusable else {}
    old_hash = dict(zip(previous.document_ids, previous.content_hashes, strict=True)) if reusable else {}
    recomputed = 0
    for left_index, left in enumerate(ordered):
        for right_index in range(left_index + 1, size):
            right = ordered[right_index]
            if (
                left.document_id in old_index
                and right.document_id in old_index
                and old_hash[left.document_id] == hashes[left_index]
                and old_hash[right.document_id] == hashes[right_index]
            ):
                value = previous.values[old_index[left.document_id], old_index[right.document_id]]
            else:
                result = score_pair(left, right, embedding_ecdf=embedding_ecdf)
                value = np.nan if result is None else result.similarity
                recomputed += 1
            values[left_index, right_index] = value
            values[right_index, left_index] = value
    return (
        SimilarityMatrix(
            document_ids=document_ids,
            lesson_keys=lesson_keys,
            content_hashes=hashes,
            values=values,
            schema_version=schema_version,
            extraction_model=extraction_model,
            weight_set=weight_set,
            ecdf_version=embedding_ecdf.version,
        ),
        recomputed,
    )
