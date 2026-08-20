from pathlib import Path


ROOT = Path(__file__).parents[2]


def test_analysis_worker_fails_closed_without_explicit_enablement():
    script = (ROOT / "ops/mini/run-analysis-worker.sh").read_text()

    assert '${ANALYSIS_ENABLED:-0}' in script
    assert '!= "1"' in script
    assert script.index("ANALYSIS_ENABLED") < script.index("kb.pipeline.analysis run")


def test_projection_worker_can_import_main_package_when_run_directly():
    script = (ROOT / "ops/mini/run-atlas-projection-worker.sh").read_text()

    assert 'export PYTHONPATH="$repo_root/src${PYTHONPATH:+:$PYTHONPATH}"' in script
