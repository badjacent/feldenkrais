"""Cold-import the built Lambda artifact without any deployment resources."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from zipfile import ZipFile
root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as directory:
    with ZipFile(root / "terraform/lambda_web.zip") as archive:
        archive.extractall(directory)
    env = {k: v for k, v in os.environ.items() if k not in {
        "DATABASE_URL", "KB_RESOURCE_DIRS", "KB_WEB_EXTENSIONS", "KB_DATABASE_MIXINS"}}
    env.update(PYTHONPATH=directory, AWS_LAMBDA_FUNCTION_NAME="package-check", AWS_EC2_METADATA_DISABLED="true")
    subprocess.run([sys.executable, "-c", "import web; assert web.app"],
                   cwd=directory, env=env, check=True)
print("Web package cold import passed")
