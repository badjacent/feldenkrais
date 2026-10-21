# Repository Layout: Backbone, Projects, Files

Work around this system falls into three kinds, each kept somewhere with rules
that fit it.

| Kind | Where | Kept by | Rules |
|---|---|---|---|
| Backbone | this repo + the `private/` submodule | git, PRs | stable, tested, changed deliberately |
| Projects | `~/github/feldenkrais-projects/<name>/` | each project's choice; backed up to S3 | anything goes |
| Files | `~/feldenkrais-files/` | S3 backup, not git | large, third-party, or generated |

## Backbone

The analyzer and the ATM database: `src/kb`, the database schema, ingestion,
terraform, tests, the HTTP API, and the ops scripts that run them.

- This repo holds all code and structure, and is meant to be publishable.
- `private/` holds only what the backbone needs but cannot publish: scrapers for
  paywalled sources, the capture extension and server, lesson texts, framework
  prompts, and private ops notes.
- The Atlas crawler and catalog (`feldenkrais-index`) is a backbone repo of its
  own, checked out beside this one at `~/github/feldenkrais-index` (the default
  `ATLAS_REPO_PATH` of `../feldenkrais-index` resolves to it).

**The backbone never depends on a project.** Nothing under `src/`, `ops/`,
`terraform/` or `private/` may import, read, or link to anything in
`feldenkrais-projects` or `feldenkrais-files`.

## Projects

Prototypes, experiments, writing, research, and products that use the backbone.
They may be poorly organized, short-lived, or abandoned; that is fine.

- One folder per project. A project may be untracked, a local-only repo, or a
  repo with its own private remote.
- A project reaches the backbone only through its published entry points: the
  database, the HTTP API, or its Python modules. To use the modules, run with
  the backbone's interpreter and `src/kb` on the path, via the
  `bin/kbpy` launcher in the projects folder, and locate the backbone with
  `FELDENKRAIS_REPO` (default `~/github/feldenkrais`), never with
  `Path(__file__).parents[n]`.
- **Graduating:** when something in a project should be relied on, move it into
  the backbone through a PR, with tests. That is the only way project code
  becomes backbone code.
- A dead project is deleted.

## Files

Material that is not ours or not code: source audio and video, PDFs and text
extracts of others' work, scraped pages, and large generated output.

- `sources/` for third-party material, `generated/` for output worth keeping,
  `scratch/` for anything else.
- Projects refer to a file by its path in the store and do not copy it.
- Caches that can be rebuilt (for example `private/data/fm_loader_page_cache/`)
  stay where the code expects them and are gitignored.

## Backup

`~/feldenkrais-files/bin/backup.sh` syncs both `~/feldenkrais-files` and
`~/github/feldenkrais-projects` to a versioned S3 bucket, skipping what can be
rebuilt (virtualenvs, `node_modules`, Swift `.build`, `__pycache__`).
