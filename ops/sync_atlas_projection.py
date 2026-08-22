#!/usr/bin/env python3
"""Safely publish the public analysis projection into the sibling Atlas repo."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

try:
    from ops.export_atlas_projection import main as export_main
except (
    ModuleNotFoundError
):  # Direct execution adds ops/, not the repo root, to sys.path.
    from export_atlas_projection import main as export_main


def run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=True, text=True, capture_output=True)


@contextmanager
def atlas_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SystemExit(
                "Atlas crawl/publish cycle is active; projection sync deferred"
            ) from exc
        yield


def validate_projection(atlas: Path, projection: Path) -> int:
    code = """
import json, sys
from pathlib import Path
from feldenkrais_index.analysis_projection import apply_analysis_projection
records = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
report = apply_analysis_projection(records, Path(sys.argv[2]))
print(json.dumps(report, sort_keys=True))
"""
    result = run(
        str(atlas / ".venv/bin/python"),
        "-c",
        code,
        str(atlas / "catalog/resources.json"),
        str(projection),
        cwd=atlas,
    )
    return int(json.loads(result.stdout)["analysis_entries"])


def export_to(catalog: Path, output: Path) -> None:
    old_argv = sys.argv
    try:
        sys.argv = [
            "export_atlas_projection.py",
            "--catalog",
            str(catalog),
            "--output",
            str(output),
        ]
        export_main()
    finally:
        sys.argv = old_argv


def projection_equivalent(left: bytes, right: bytes) -> bool:
    """Ignore the export timestamp when the public analysis has not changed."""
    first = json.loads(left)
    second = json.loads(right)
    first.pop("generated_at", None)
    second.pop("generated_at", None)
    return first == second


def validate_count_change(previous: int, candidate: int, *, minimum_ratio: float = 0.8) -> None:
    if candidate <= 0:
        raise ValueError("refusing to publish an empty Atlas analysis projection")
    if previous > 0 and candidate < previous * minimum_ratio:
        raise ValueError(
            f"refusing projection shrink from {previous} to {candidate} entries"
        )


def crawl_lock_path(atlas: Path) -> Path:
    """Return the exact lock file held by the Atlas crawl/publish cycle."""
    return atlas / "var/crawl-cycle.lock"


def sync(atlas: Path, *, push: bool) -> dict:
    atlas = atlas.resolve()
    target = atlas / "catalog/chooser-analysis.json"
    with atlas_lock(crawl_lock_path(atlas)):
        if run("git", "status", "--porcelain", cwd=atlas).stdout.strip():
            raise SystemExit("Atlas worktree is not clean; projection sync deferred")
        branch = run("git", "branch", "--show-current", cwd=atlas).stdout.strip()
        if branch != "main":
            raise SystemExit(f"Atlas projection sync requires main, found {branch!r}")
        run("git", "pull", "--ff-only", "origin", "main", cwd=atlas)
        with tempfile.TemporaryDirectory(prefix="atlas-projection-") as directory:
            candidate_dir = Path(directory) / "catalog"
            candidate_dir.mkdir()
            candidate = candidate_dir / "chooser-analysis.json"
            resources = candidate_dir / "resources.json"
            manifest = candidate_dir / "manifest.json"
            shutil.copy2(atlas / "catalog/resources.json", resources)
            shutil.copy2(atlas / "catalog/manifest.json", manifest)
            export_to(resources, candidate)
            entries = validate_projection(atlas, candidate)
            previous_entries = 0
            if target.is_file():
                previous_payload = json.loads(target.read_bytes())
                previous_entries = len(
                    previous_payload.get("entries") or previous_payload.get("lessons") or []
                )
            validate_count_change(previous_entries, entries)
            if target.is_file() and projection_equivalent(
                target.read_bytes(), candidate.read_bytes()
            ):
                return {"changed": False, "entries": entries, "pushed": False}
            run(
                str(atlas / ".venv/bin/python"),
                "scripts/build_site.py",
                "--resources",
                str(resources),
                "--manifest",
                str(manifest),
                "--mode",
                "approved",
                "--output",
                "var/site-projection-check",
                cwd=atlas,
            )
            target.write_bytes(candidate.read_bytes())
        run("git", "add", "catalog/chooser-analysis.json", cwd=atlas)
        run(
            "git",
            "commit",
            "-m",
            f"data: publish analysis for {entries} lessons",
            cwd=atlas,
        )
        if push:
            run("git", "push", "origin", "main", cwd=atlas)
        return {"changed": True, "entries": entries, "pushed": push}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--atlas",
        type=Path,
        default=Path(os.getenv("ATLAS_REPO_PATH", "../feldenkrais-index")),
    )
    parser.add_argument("--no-push", action="store_true")
    args = parser.parse_args()
    if not os.getenv("DATABASE_URL"):
        parser.error("DATABASE_URL is required")
    print(json.dumps(sync(args.atlas, push=not args.no_push), sort_keys=True))


if __name__ == "__main__":
    main()
