# Analyzer contributor guide

This repository contains the lesson analyzer, ingestion and transcription pipeline,
search, and conversation UI. Read README.md and docs/REPO_LAYOUT.md first.

- Keep sample data fictional and include its provenance.
- Never commit credentials, databases, audio collections, or personal data.
- Resource overrides and optional components are selected only by deployment configuration.
- Run `uv run --extra web --extra worker pytest tests/unit` for application changes.
- Preserve the Atlas projection contract and existing database compatibility.
- Do not run ingestion, paid model calls, production migrations, deploys, or publication
  commands without an explicit request. Use fixtures for tests.
- Do not commit or push unless asked.
