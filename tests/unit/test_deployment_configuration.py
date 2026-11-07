"""Regression tests for explicit deployment boundaries."""
import os
from pathlib import Path
import subprocess
import sys

import pytest
from resources import resolve_resource

ROOT = Path(__file__).resolve().parents[2]


def test_resource_precedence_and_fallback(tmp_path, monkeypatch):
    bundled, first, second = [tmp_path / name for name in ("bundled", "first", "second")]
    for directory in (bundled, first, second):
        directory.mkdir()
    (bundled / "a").write_text("default")
    (first / "a").write_text("override")
    (second / "b").write_text("second")
    (bundled / "c").write_text("fallback")
    monkeypatch.setenv("KB_RESOURCE_DIRS", os.pathsep.join(map(str, (first, second))))
    assert resolve_resource(bundled, "a") == first / "a"
    assert resolve_resource(bundled, "b") == second / "b"
    assert resolve_resource(bundled, "c") == bundled / "c"
    monkeypatch.delenv("KB_RESOURCE_DIRS")
    assert resolve_resource(bundled, "a") == bundled / "a"


def test_bad_configuration_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("KB_RESOURCE_DIRS", str(tmp_path / "missing"))
    with pytest.raises(ValueError):
        resolve_resource(tmp_path, "a")
    monkeypatch.delenv("KB_RESOURCE_DIRS")
    for invalid in ("../a", "/a"):
        with pytest.raises(ValueError):
            resolve_resource(tmp_path, invalid)


def run_web(code, extra_path, **configuration):
    env = {key: value for key, value in os.environ.items() if key not in {
        "DATABASE_URL", "KB_RESOURCE_DIRS", "KB_WEB_EXTENSIONS", "KB_DATABASE_MIXINS"}}
    env.update(PYTHONPATH=os.pathsep.join(map(str, (ROOT / "src", ROOT / "src/kb", extra_path))),
               AWS_EC2_METADATA_DISABLED="true", **configuration)
    return subprocess.run([sys.executable, "-c", code], cwd=extra_path, env=env,
                          text=True, capture_output=True)


def test_explicit_components_load_and_defaults_do_not(tmp_path):
    (tmp_path / "sample_component.py").write_text(
        "class ExtraDatabaseMethods:\n    component_marker = 'loaded'\n"
        "def register(web):\n    web.app.state.component_marker = 'loaded'\n")
    code = "import web; from db import Database; assert not hasattr(web.app.state, 'component_marker'); assert not hasattr(Database, 'component_marker')"
    result = run_web(code, tmp_path)
    assert result.returncode == 0, result.stderr
    code = "import web; from db import Database; assert web.app.state.component_marker == Database.component_marker == 'loaded'"
    result = run_web(code, tmp_path,
        KB_WEB_EXTENSIONS="sample_component:register",
        KB_DATABASE_MIXINS="sample_component:ExtraDatabaseMethods")
    assert result.returncode == 0, result.stderr
    result = run_web("import web", tmp_path, KB_WEB_EXTENSIONS="missing_component:register")
    assert result.returncode != 0 and "missing_component" in result.stderr
