# Feldenkrais Atlas — base known delta

Last evidence refresh: 2026-08-20. This is the sparse, materialized difference
between the current Atlas and the governing ideal. It records facts, not the
temporary work plan or speculative consequences.

## Governing ideal

Atlas increasingly maps publicly accessible Feldenkrais lessons, keeps that map
fresh, and publishes only safe public metadata and reviewed derived facets. A
three-round chooser uses meaningful lesson signals while excluding renditions.
Discovery, persistence, annotation, publication, and observability operate
continuously with bounded cost and fail-closed safety.

Non-negotiable boundaries:

- The crawler never publishes media, transcripts, substantial source text,
  prompts, raw model output, credentials, or private operational state.
- The crawler does not transcribe. Transcript-derived analysis remains in the
  main knowledge system and reaches Atlas only through a versioned public
  projection for confirmed publicly accessible lessons.
- No lesson analysis or analysis-queue execution occurs without owner approval.
- Seed proposal and owner activation are separate audited actions.
- Absence changes freshness only after a complete authoritative sweep.
- Code changes use reviewed releases; only validated catalog-data publication is
  automated.

## Current facts and delta

| Area | Current evidence | Base delta |
|---|---|---|
| Public catalog | 1,767 public records; 853 live free audio/video lessons; 57 lesson sources in the live interactive index | Coverage is substantial but not demonstrably close to the discoverable universe |
| Discovery state | Mini health: 93 sources; 480 queued, 65 partial, 5 failed tasks; last 45-minute cycle accepted 86 and deferred 52 seeds by time budget | Known queues and partial frontiers remain; failed tasks require bounded reconciliation |
| Continuous crawl | `com.feldenkrais.atlas-crawler` is loaded every 6 hours; last run succeeded and auto-published | Long-run yield, failure drift, and queue retirement are not yet summarized as a stable operating SLO |
| Freshness | Complete-sweep auditing is active; latest run marked 7 stale and 0 dead | Full source-family coverage and repeated-sweep behavior remain to be measured |
| Backups | Latest crawl produced, locally verified, uploaded, and remotely verified an encrypted S3 backup | Restore testing and a documented recovery drill remain unverified |
| Canonical crawler DB | Mini health reports SQLite | Planned Neon cutover, PITR verification, and post-cutover restore evidence remain incomplete |
| Lightweight annotations | 634/853 lessons have semantic metadata (74.3%); 508 have facets (59.6%); 477 have at least three chooser signals (55.9%) | Safe metadata coverage is incomplete and uneven |
| Transcript-derived profiles | 466 eligible analyzed ATMs in the main knowledge DB; 157 confirmed public Atlas matches | Most public lessons do not have a confirmed transcript-derived profile; further analysis is approval-gated |
| Similarity engine | Rendition gate, versioned profiles, ECDF-calibrated measure, retrieval operators, matrix artifact, and public neighbor projection are implemented | Dedicated trademark/architecture extraction is absent; analysis execution remains intentionally unapproved |
| Live chooser | Deployed three-round chooser and 157 projected neighbor lists | Live compact-index serialization currently drops neighbor lists, so production falls back to heuristics |
| Projection maintenance | A 6-hour Atlas projection launch agent is loaded | Worker currently fails before export because `PYTHONPATH` is missing |
| Analysis boundary | Hourly analysis launch agent is loaded; recent attempts completed no work or failed for absent `GOOGLE_API_KEY` | The agent must be disabled while approval is absent; failed attempts are still contrary to the boundary |
| Local transcription | Mini transcription worker is active and completing local Whisper jobs | Authorized transcription capacity exists, but it must remain distinct from analysis and public projection |
| Analytics/cost | Privacy-preserving CloudFront logging, request alarm, and $10 budget policy are documented | Current traffic/cost reports and periodic review evidence have not been refreshed in this audit |
| Governance | Owner-only seed review CLI and audited candidate states exist | Five newly proposed sources and the wider candidate backlog still require owner decisions before activation |

## Evidence sources

- Live `https://feldenkraisatlas.com/` and `data/search-index.json`
- Mini `feldenkrais-index health`, launchd state, worker logs, crawl-cycle logs,
  backup/upload output, and Git history
- `docs/SIMILARITY_{SYSTEM,AMENDMENTS,MEASURES}.md`
- Atlas `OPERATIONS.md`, `TODO.md`, `PUBLIC_SCHEMA.md`, `FRESHNESS.md`,
  `DISCOVERY_STRATEGY.md`, and `ATM_ANALYSIS_INTEGRATION_PLAN.md`

Refresh this file when facts change. Keep consequences, priorities, and chosen
attention outside it.
