"""The analysis worker's local wake file.

The worker switches a model profile off once it has nothing left to run, and
when no profile is left on it removes this file. Its runner checks for the file
before doing anything else and exits at once without it, so an idle worker
never queries the database (and never wakes Neon). Switching a profile on, or
re-running a step, from the analyzer on the same machine creates the file
again; so does ``touch`` by hand. That is the manual gate restart.

The path comes from ANALYSIS_WAKE_FILE, which the Mini's runners set. Without
it nothing here does anything, so other deployments behave as before.
"""

from __future__ import annotations

import os
from pathlib import Path

ENV = "ANALYSIS_WAKE_FILE"


def wake_file() -> Path | None:
    path = os.environ.get(ENV)
    return Path(path).expanduser() if path else None


def wake() -> None:
    """Let the worker start again on its next scheduled run."""
    path = wake_file()
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()


def sleep() -> None:
    """Stop the worker's scheduled runs from touching the database."""
    path = wake_file()
    if path:
        path.unlink(missing_ok=True)
