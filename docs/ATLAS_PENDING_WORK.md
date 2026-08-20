# Feldenkrais Atlas — pending work

Last reconciled: 2026-08-20. This is a practical backlog derived from
`ATLAS_BASE_KNOWN_DELTA.md` and `ATLAS_OPERATING_PLAN.md`; those files remain
authoritative for the sparse delta and current attention map.

## Immediate closeout

1. Let the active bounded Mini crawl finish.
2. Verify crawl statistics, failures, queue retirement and newly deferred work.
3. Verify the local backup, exact S3 upload, catalog publication, live record
   counts, and chooser data.
4. Pull current Atlas `main` onto the Mini; it intentionally remained on
   `a3d9ad8` while the cycle was active.
5. Run the new isolated S3 restore drill and catalog-quality health report in
   the production environment.
6. Refresh the base known delta with the completed-cycle evidence and choose W1
   discovery or W2 metadata quality as the next primary attention stream.

## Discovery completeness

- Retire claimable retries and continuations while measuring cohort age and
  retirement, not raw queue size alone.
- Verify authoritative maintenance behavior for YouTube, SoundCloud,
  RSS/podcasts, HTML directories, sitemaps, OpenATM, Feldenkrais Project,
  Kinesophics, and Feldy Notebook-derived sources.
- Sample accepted and rejected results by lane for classification drift.
- Gradually review the 404 owner-attention source proposals. Do not activate
  them automatically or count them as crawler debt.
- Expand multilingual search and seed discovery again after present source
  tails stabilize.

## Catalog metadata quality

Baseline among the last verified 923 live free lessons:

| Field | Coverage |
|---|---:|
| Duration | 73% |
| Teacher | 39% |
| Source description | 25% |
| Facets | 59% |
| At least three chooser signals | 58% |
| Native identity | 79% |

- Recover duration, teacher, source description, and **language** from
  source-provided metadata before using derived inference.
- Prioritize SoundCloud and ordinary websites, where missingness is greatest.
- Track coverage by language and normalize language codes consistently.
- Review bounded samples of `other` classifications and implausibly short
  lessons.
- Resolve the known duplicate YouTube URL variant and other bounded identity
  anomalies without inventing teacher or description data.

## Freshness

- Audit the seven latest genuine stale transitions against complete
  authoritative sweeps.
- Install the merged transition-telemetry fix on the Mini; historical
  dead-to-dead observations inflated `resources_marked_dead` without changing
  catalog state.
- Observe stale, dead, and revived behavior across at least two scheduled sweep
  windows.
- Confirm partial, capped, failed, blocked, and rolling-feed crawls never create
  absence evidence.

## Similarity and chooser

- Add normalized content language to both the full profile/matrix score and the
  chooser fallback as a small objective metadata term. Missing/`unknown`
  language must not penalize a pair.
- Rebuild the versioned profiles, calibration, matrix, and Atlas projection from
  existing cached inputs; this must not invoke analysis.
- Verify the live chooser uses the new projected version and retains safe
  fallback behavior for lessons without profiles.
- Increase safe title/description-derived signal coverage and monitor scheduled
  projection maintenance.
- Later collect privacy-preserving chooser decisions as possible weight-learning
  evidence. The metric remains replaceable.

## Analysis boundary

The following remains explicitly owner-approval-gated:

- selecting an analysis cohort and budget;
- trademark, architecture, or other transcript-analysis extraction;
- executing the analysis queue; and
- enabling any unattended analysis worker.

The analysis launch agent remains disabled. Existing cached analysis may be
projected, but no new lesson analysis may run without approval.

## Persistence and recovery

- Decide the Neon cutover window separately from crawler expansion.
- Repeat the empty-target SQLite-to-Neon migration and compare every table count.
- Test Neon point-in-time recovery and perform a timed rollback/recovery drill.
- Continue verified SQLite plus S3 backups until the Neon recovery path is
  proven.

## Public operation and observability

- Refresh the seven-day CloudFront traffic report and confirm budget/alarm
  recipients.
- Define thresholds for crawl failures, rejection drift, oldest worker task,
  backup age, publication failure, and genuine freshness-transition spikes.
- Keep the no-client-analytics-JavaScript and no-automatic-cost-cutoff policies.

