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
| Public catalog | 1,922 public records; 923 live free audio/video lessons; 57 lesson sources in the live interactive index | Coverage is substantial but not demonstrably close to the discoverable universe |
| Discovery state | Mini report: 93 enabled sources; the first measured claimable backlog was 76 (58 retry, 18 continuation). A productive 45-minute run retired 20 retries and 17 continuations but durably queued 60 newly time-deferred candidates, ending at 79 claimable tasks (78 retry, 1 continuation); 404 owner-review proposals remain separate | Raw queue count can rise while a useful bounded run exposes a large source tail; cohort age/retirement and yield must accompany count, and owner-review proposals must never be treated as worker debt |
| Continuous crawl | `com.feldenkrais.atlas-crawler` is loaded every 6 hours; the latest completed run succeeded, verified its S3 backup, and auto-published; Ctrl-C/SIGTERM and stale-run reconciliation now record `interrupted` truthfully | Stable operating thresholds and repeated evidence that no new abandoned `running` rows appear remain unresolved |
| Freshness | Complete-sweep auditing is active; latest completed run performed 26 complete sweeps and marked 0 stale, 0 dead, and 0 revived | Full source-family coverage and repeated-sweep behavior remain to be measured |
| Backups | Latest crawl produced, locally verified, uploaded, and remotely verified an encrypted S3 backup; the exact `20260820T121330Z` S3 object was downloaded and restored in isolation with clean SQLite integrity and foreign keys (93 sources, 1,862 resources, 2,219 tasks, 84 runs, 35,432 audit events) | The restore procedure is being made repeatable in Atlas PR #130; a timed full recovery/rollback exercise remains unverified |
| Canonical crawler DB | Mini health reports SQLite | Planned Neon cutover, PITR verification, and post-cutover restore evidence remain incomplete |
| Lightweight annotations | 704/923 lessons have semantic metadata (76.3%); 544 have facets (58.9%); 535 have at least three chooser signals (58.0%) | Safe metadata coverage is incomplete and uneven |
| Transcript-derived profiles | 466 eligible analyzed ATMs in the main knowledge DB; 160 confirmed public Atlas matches | Most public lessons do not have a confirmed transcript-derived profile; further analysis is approval-gated |
| Similarity engine | Rendition gate, versioned profiles, ECDF-calibrated measure, retrieval operators, matrix artifact, and public neighbor projection are implemented | Dedicated trademark/architecture extraction is absent; analysis execution remains intentionally unapproved |
| Live chooser | Deployed three-round chooser; live compact index contains neighbor lists for 160/160 profiled public lessons and invokes projected scoring | Similarity coverage is 17.3% of the 923 live free lessons; the remainder use the safe heuristic fallback |
| Projection maintenance | The 6-hour launch agent completed an idempotent run with 160 entries and `changed:false` after a manual publication | Continued success should be monitored with ordinary scheduled-worker evidence |
| Analysis boundary | `com.feldenkrais.analysis` is persistently disabled; the wrapper also exits before queue access unless `ANALYSIS_ENABLED=1` | No owner-approved analysis cohort or budget currently exists |
| Local transcription | Mini transcription worker is active and completing local Whisper jobs | Authorized transcription capacity exists, but it must remain distinct from analysis and public projection |
| Analytics/cost | Privacy-preserving CloudFront logging, request alarm, and $10 budget policy are documented | Current traffic/cost reports and periodic review evidence have not been refreshed in this audit |
| Governance | Owner-only seed review CLI and audited candidate states exist; 404 queued proposals are explicitly reported as owner attention | Proposal sampling/prioritization and eventual owner decisions remain; none may be activated as crawler backlog retirement |

## Evidence sources

- Live `https://feldenkraisatlas.com/` and `data/search-index.json`
- Mini `feldenkrais-index health`, launchd state, worker logs, crawl-cycle logs,
  backup/upload output, and Git history
- `docs/SIMILARITY_{SYSTEM,AMENDMENTS,MEASURES}.md`
- Atlas `OPERATIONS.md`, `TODO.md`, `PUBLIC_SCHEMA.md`, `FRESHNESS.md`,
  `DISCOVERY_STRATEGY.md`, and `ATM_ANALYSIS_INTEGRATION_PLAN.md`

Refresh this file when facts change. Keep consequences, priorities, and chosen
attention outside it.
