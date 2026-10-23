"""Cold-start guard for the web Lambda.

The Lambda layer (terraform/lambda_web_requirements.txt) is deliberately small:
fastapi, mangum, multipart, dotenv, the three LLM SDKs, pydantic, psycopg2.
PyYAML and numpy are NOT in it, and the package does not ship the YAML
registries either. A module-level `import yaml` anywhere in the start-up chain
therefore takes the whole site down with

    Runtime.ImportModuleError: Unable to import module 'web': No module named 'yaml'

which is exactly what happened once. Keep those imports inside the functions
that need them.
"""

import subprocess
import sys
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
KB = REPO / "src" / "kb"

# Third-party packages the web Lambda layer does not provide.
ABSENT_IN_LAMBDA = ("yaml", "numpy")


def _import_web_without(packages) -> subprocess.CompletedProcess:
    script = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(KB)!r})
        blocked = {tuple(packages)!r}

        class Block:
            def find_module(self, name, path=None):
                return self if name.split('.')[0] in blocked else None

            def load_module(self, name):
                raise ImportError("No module named %r (simulated Lambda)" % name)

        sys.meta_path.insert(0, Block())
        from dotenv import load_dotenv
        load_dotenv({str(REPO / '.env')!r})
        import web
        assert web.app.routes
        print("ok")
    """)
    return subprocess.run([sys.executable, "-c", script], capture_output=True,
                          text=True, cwd=str(REPO))


def test_web_imports_without_packages_the_lambda_layer_lacks():
    result = _import_web_without(ABSENT_IN_LAMBDA)
    assert result.returncode == 0, (
        "web.py's import chain needs a package the Lambda layer does not have:\n"
        + result.stderr[-2000:])
    assert "ok" in result.stdout
