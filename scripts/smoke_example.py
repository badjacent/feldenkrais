"""Check a synthetic example against an explicitly supplied disposable database."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--database-url', required=True)
args = parser.parse_args()
if urlparse(args.database_url).hostname not in {'localhost', '127.0.0.1', '::1'}:
    parser.error('Use a disposable local database')
for _ in range(2):
    subprocess.run([sys.executable, str(ROOT / 'scripts/seed_example.py'),
                    '--database-url', args.database_url], check=True)
os.environ['DATABASE_URL'] = args.database_url
os.environ['AWS_EC2_METADATA_DISABLED'] = 'true'
for name in ('KB_RESOURCE_DIRS', 'KB_WEB_EXTENSIONS', 'KB_DATABASE_MIXINS'):
    os.environ.pop(name, None)
sys.path.insert(0, str(ROOT / 'src/kb'))
from fastapi.testclient import TestClient
import web
with web._db.get_cursor(commit=False) as cursor:
    cursor.execute("SELECT id FROM document WHERE source_url = %s",
                   ('https://example.invalid/synthetic-lesson',))
    documents = cursor.fetchall()
assert len(documents) == 1, 'Seeding must be idempotent'
with TestClient(web.app, base_url='http://127.0.0.1') as client:
    for url in ('/', '/analysis', '/ingestion', f"/lesson/{documents[0]['id']}"):
        response = client.get(url)
        assert response.status_code == 200, (url, response.status_code)
    response = client.get('/examples/sample.wav')
    assert response.status_code == 200 and response.content.startswith(b'RIFF')
    response = client.get(f"/api/lesson/{documents[0]['id']}/combined")
    assert response.status_code == 200 and len(response.json()['steps']) == 2
    lesson = client.get(f"/lesson/{documents[0]['id']}")
    assert 'Compare models' not in lesson.text
print('Synthetic example: idempotent seed, four application pages, audio and saved analysis passed')
