# Repository layout

- `src/kb/`: analyzer UI, database methods, lesson ingestion, transcription, search,
  conversation, and the Atlas export contract.
- `data/`: application prompts, schemas, and original example resources.
- `tests/`: unit and integration tests; database tests need disposable PostgreSQL.
- `ops/dev/`: local development entry points.
- `terraform/`: portable Lambda package builders, dependency manifests, and checks.

The application runs independently. Deployments can set `KB_RESOURCE_DIRS` to an
ordered, platform-separated list of absolute resource directories; first match
wins, followed by bundled defaults. A configured missing directory is an error.
`KB_WEB_EXTENSIONS=module:register` loads trusted Python callables with the web
module as their argument. `KB_DATABASE_MIXINS=module:Class` adds optional database
methods at process startup. Multiple components use comma-separated values.
These are process configuration, never request input. Deployments install their
own modules and resource files; this repository does not locate or fetch them.

The Atlas website is a separate application. This repository retains only the
versioned export contract and projection code used to supply analysis results.
