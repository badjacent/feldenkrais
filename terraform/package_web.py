"""Build the web artifact from explicit application/resource inputs."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
root = Path(__file__).resolve().parents[1]
source = root / "src/kb"
output = root / "terraform/lambda_web.zip"
with ZipFile(output, "w", ZIP_DEFLATED) as archive:
    for path in sorted(source.rglob("*.py")):
        relative = path.relative_to(source)
        if not set(relative.parts) & {"__pycache__", "ingest", "search", "ops"}:
            archive.write(path, str(relative))
    for line in (root / "terraform/web_resources.txt").read_text().splitlines():
        relative = Path(line)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"Invalid resource path: {line}")
        archive.write(root / "data" / relative, "data/" + str(relative))
    for line in (root / "terraform/web_docs.txt").read_text().splitlines():
        relative = Path(line)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"Invalid documentation path: {line}")
        archive.write(root / "docs" / relative, "docs/" + str(relative))
    for path in sorted((source / "extract_map/data").rglob("*")):
        if path.is_file():
            archive.write(path, str(path.relative_to(source)))
print(output)
