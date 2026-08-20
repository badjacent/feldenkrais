# Feldenkrais Atlas — operating plan

Derived from `ATLAS_BASE_KNOWN_DELTA.md`. This is an attention map, not another
source of project truth. Workstreams are temporary and may be split, merged, or
retired as the delta changes.

## Planning decisions

1. Protect explicit boundaries before increasing throughput.
2. Make the deployed chooser actually consume the implemented similarity signal
   before tuning that signal.
3. Keep crawling and lightweight annotation running while transcript analysis is
   paused.
4. Prefer measured queue retirement and source-family coverage over undirected
   longer runs.
5. Treat SQLite + verified S3 backup as an acceptable current operating state,
   but not the final durability design; plan Neon as a separate reversible cutover.
6. Do not let local worktree cleanliness be a hidden dependency of unattended
   projection or publication.

## Present workstreams

### W0 — Safety and operational correctness (current attention)

Status: **completed 2026-08-20**. Analysis scheduling was disabled without
changing queue contents; the worker now fails closed behind
`ANALYSIS_ENABLED=1`; Atlas serialization and production deployment retain
160/160 neighbor lists; and the repaired projection launch agent completed an
idempotent `changed:false` run. Recheck the disabled launchd state after the
former hourly interval as an operational confirmation.

Outcome: unattended jobs obey owner boundaries and the deployed similarity path
works end to end.

Sequence:

1. Unload and disable `com.feldenkrais.analysis` without changing analysis queue
   contents; prove it stays unloaded across its former hourly interval.
2. Merge and deploy Atlas PR #121 only if CI, review deployment, and a
   production-shaped build prove all 157 neighbor lists survive serialization.
3. Verify production contains `analysis_similarities` and that chooser scoring
   uses projected similarity for a known pair.
4. Add the missing main-repo `PYTHONPATH` setup to the projection worker; test it
   in no-change mode, then prove the scheduled launch agent exits successfully.
5. Record worker enablement policy so analysis cannot be re-enabled as a side
   effect of installing the other Mini agents.

Exit criteria:

- Analysis launch agent is absent/disabled and no new attempt appears.
- 157/157 profiled public lessons retain neighbor data in production.
- Projection worker completes successfully and is idempotent.

### W1 — Discovery coverage and queue retirement

Outcome: the catalog approaches the discoverable free-lesson universe through
bounded, auditable work rather than indefinite broad crawling.

- Report queue age, task family, source family, partial-frontier depth, retry
  exhaustion, and yield over rolling 1/7/30-day windows.
- Continue scheduled bounded cycles while yield is healthy.
- Prioritize 52 time-deferred seeds and 65 partial tasks before adding broad new
  search families.
- Review five proposed sources through the owner-only boundary; do not activate
  them automatically.
- Maintain explicit coverage lanes for YouTube, SoundCloud, podcasts/RSS,
  HTML/directories, sitemaps, OpenATM, Feldenkrais Project, Kinesophics, and
  Feldy Notebook-derived sources.
- Sample accepted/rejected outputs per lane to detect classification drift.

Exit criteria are recurring measurements, not “all web content found”: queues
are aging downward, mature-source yield is low and stable, and each supported
source family has an authoritative maintenance strategy.

### W2 — Metadata quality and freshness

Outcome: public records remain useful and honest as sources change.

- Raise duration, teacher, source-description, native-identity, and facet
  coverage with source-provided facts first and lightweight enrichment second.
- Audit the seven latest stale transitions against complete-sweep evidence.
- Track revival/death behavior across at least two scheduled sweep windows.
- Add quality sampling for `other` classification and implausibly short lessons.

### W3 — Annotation and similarity coverage

Outcome: the chooser increasingly uses meaningful lesson-level signals without
crossing the analysis approval boundary.

- Continue safe title/description-based semantic annotation for the remaining
  public lessons.
- Refresh the public projection whenever confirmed crosswalk/profile inputs
  change.
- Do not run trademark, architecture, or other transcript-analysis extraction
  until the owner explicitly approves analysis execution.
- When approved, stage a small reviewed extraction cohort before any unattended
  analysis worker is enabled.
- Collect chooser decisions later as weight-learning evidence; do not block the
  current chooser on the final arbitrary metric.

### W4 — Persistence and recovery

Outcome: crawler state survives Mini loss and has a practiced recovery path.

- Verify an exact S3 backup can be downloaded and restored into an isolated DB.
- Decide the Neon cutover window separately from crawler expansion.
- Re-run empty-target migration plus row-count verification, test Neon PITR, then
  retain SQLite backups through a successful rollback drill.

### W5 — Public operation and observability

Outcome: deployment, traffic, cost, and failures are visible without tracking
people.

- Refresh the seven-day CloudFront report and confirm alarms/budget recipients.
- Define lightweight operating thresholds for crawl failures, rejection drift,
  publication failure, queue age, and backup age.
- Keep the no-analytics-JavaScript and no-automatic-cost-cutoff decisions.

## Dependency order

```text
W0 operational correctness
  ├── enables trustworthy W3 similarity publication
  └── protects all unattended work

W1 discovery ──> W2 metadata/freshness ──> W3 coverage
       └──────────────> W4 state volume/recovery pressure

W4 durability and W5 observability support every scheduled workstream.
```

## Next decision checkpoint

After W0 exits, inspect one week of scheduled-crawl evidence and choose one
primary attention stream. **Current choice: W1**, because 480 queued tasks, 65
partial tasks, and 52 time-deferred seeds are the largest evidenced active
delta. W2 quality sampling runs alongside W1 reporting, but does not replace
queue retirement as the primary attention.

- W1 if valuable queues remain high-yield or old;
- W2 if catalog quality/freshness is the limiting user value;
- W4 if recovery evidence is still missing;
- W3 only after an explicit analysis decision or when safe lightweight coverage
  remains the clear bottleneck.
