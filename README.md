# Feldenkrais lesson analyzer

A personal application for organizing lesson material, transcribing audio,
analyzing lesson structure, searching a collection, and discussing it through
an LLM-backed chat interface. It is intended for an individual working with
material they have permission to use. The Atlas website is a separate project;
this application supplies its versioned analysis export interface.

Built with Python, FastAPI, PostgreSQL/pgvector, and optional model-provider APIs.
The public example is entirely synthetic. No lesson collection, user database,
provider credential, or recording is included.

## Run locally

Requires Python 3.11+ (3.12 recommended), uv, and Docker Compose.

```sh
uv sync --extra web --extra worker
cp .env.example .env
docker compose up -d --wait
uv run python scripts/seed_example.py --database-url postgresql://analyzer:local-example-only@127.0.0.1:55432/analyzer
uv run --extra web uvicorn web:app --app-dir src/kb --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000 and the `/lesson/...` link printed by the seed command.
The original one-second test tone is at http://127.0.0.1:8000/examples/sample.wav;
it is a playback fixture, not a lesson recording. Saved synthetic analysis lets
you inspect the lesson steps without a model call.

Seeding is idempotent and restricted to a local database. Browsing the example
and its transcript needs no AWS or model credentials. Generating new analyses,
embeddings or chat responses requires a configured provider; audio ingestion
also requires storage and transcription configuration. No live jobs run during
setup. The built-in Observer framework is a minimal original example.

With an existing local PostgreSQL installation, enable pgvector and use its
connection URL instead of Docker. The app initializes its schema on startup.
Use a dedicated development database. Stop the example with `docker compose down`;
add `--volumes` only when you want to delete that example database.

## Tests and packaging

```sh
uv run --extra web --extra worker pytest tests/unit
bash terraform/build_web_lambda.sh
```

Tests mock external services. Additional tests under `tests/` include integration
checks with their own prerequisites. The Lambda builder creates a local package
and performs no deployment. Public CI runs tests and verifies the package.

See [repository layout](docs/REPO_LAYOUT.md) for resource overrides and trusted
extension hooks. See [roadmap](ROADMAP.md) for proposed application work.
Do not add personal data or third-party lesson content to fixtures. MIT license;
see [LICENSE](LICENSE).
