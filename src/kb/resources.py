"""Resolve application resources using explicit deployment configuration."""

import os
from pathlib import Path


def resolve_resource(default_dir: Path, relative_path: str) -> Path:
    """Search configured directories in order, then the built-in defaults.

    KB_RESOURCE_DIRS uses the platform path separator. Configured directories
    must exist. Callers retain control of whether an individual file is optional.
    """
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Resource paths must be relative and stay inside their root")
    roots = [Path(value).expanduser() for value in
             os.environ.get("KB_RESOURCE_DIRS", "").split(os.pathsep) if value]
    for root in roots:
        if not root.is_absolute() or not root.is_dir():
            raise ValueError(f"Resource directory must exist and be absolute: {root}")
    for root in [*roots, Path(default_dir)]:
        candidate = root / relative
        if candidate.exists():
            return candidate
    return Path(default_dir) / relative
