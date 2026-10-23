# NYC crawler administration in the authenticated site

Status: implementation handoff  
Owner: Feldenkrais Atlas  
Operational writer: Mac Mini crawler  
Human-maintenance UI: authenticated `aionyourside.net` application

## Objective

Add a private administration surface for the NYC-area live Feldenkrais directory.
The Mini continues to discover and crawl. The authenticated site lets the owner
review what it found, maintain provider and venue policy, inspect evidence and
failures, and understand whether the weekly search is covering useful territory.

This is not a public NYC listing and must not alter the Atlas publication gate.

## Database boundary

Use the application's existing Neon `DATABASE_URL`. NYC tables live in the
PostgreSQL schema `atlas_nyc`, not in the application's default schema. Always use
schema-qualified table names or set a transaction-local search path explicitly.

The canonical tables are:

- `atlas_nyc.nyc_provider`
- `atlas_nyc.nyc_venue`
- `atlas_nyc.nyc_offering`
- `atlas_nyc.nyc_occurrence`
- `atlas_nyc.nyc_event_evidence`
- `atlas_nyc.nyc_directory_decision`
- `atlas_nyc.nyc_discovery_run`
- `atlas_nyc.nyc_discovery_observation`
- `atlas_nyc.nyc_crawl_request`
- `atlas_nyc.discovery_task` (only `task_type = 'nyc_schedule_review'` belongs to this feature)

The site must not call `CREATE TABLE` in request handling. Atlas migrations own
this schema. The site should fail with a clear admin-only diagnostic if the schema
version it expects is absent.

## Authorization

Mount all routes beneath `/admin/nyc` and require the existing authenticated admin
role. A signed-in non-admin receives `403`; an anonymous user follows the existing
login flow. Never expose extracted text, prompts, crawler errors, decision history,
or candidate records through a public route or public JSON endpoint.

Every mutation must include the authenticated actor identifier and create an audit
row in the same database transaction.

## Screens

### `/admin/nyc`

An operational overview containing:

- current provider, venue, offering, occurrence, and pending-task counts;
- the latest weekly discovery run, its duration and disposition totals;
- crawler checks and failures in the last hour, 24 hours, and seven days;
- providers due now by tier;
- upcoming confirmed occurrences and records needing review;
- links to providers, venues, discovery runs, tasks, and decision history.

### `/admin/nyc/providers`

Show candidate, active, and retired providers. Support filtering by tier, status,
area, due state, and free-text name/URL. Each row should show canonical URL,
schedule URLs, last crawl, most recent result, current tier, review rationale, and
new discovery evidence.

Permitted owner actions:

- approve as A (daily), B (7 days), or C (30 days);
- reject as D (never check);
- change A/B/C tier;
- correct display name and schedule URLs;
- merge an obvious duplicate into a canonical provider;
- defer an unresolved candidate without pretending it is dead.
- request an expedited check of an already-approved provider.

Do not infer approval from editing a URL. Approval is a separate explicit action.

## Expedited check signal

The site must not poll Neon to wake the Mini. For an active A/B/C provider, expose
an owner-only **Check soon** action. After committing any policy change, write an
AES-256-encrypted JSON object to:

```text
s3://feldenkrais-prod/control/atlas-nyc/requests/<timestamp>-<request_id>.json
```

The exact version-1 payload is:

```json
{
  "version": 1,
  "action": "crawl_provider",
  "provider_id": "UUID from atlas_nyc.nyc_provider",
  "requested_by": "authenticated actor identifier",
  "requested_at": "ISO-8601 UTC timestamp",
  "request_id": "new UUID"
}
```

Do not include a URL or command supplied by the browser. The provider UUID must
come from the server-loaded record. The web Lambda needs `s3:PutObject` only on
`arn:aws:s3:::feldenkrais-prod/control/atlas-nyc/requests/*`.

The Mini checks this prefix every five minutes without touching Neon when it is
empty. Results appear under `completed/`, `failed/`, `rejected/`, or `dead/`, and
durable status appears in `atlas_nyc.nyc_crawl_request`. The UI may read the Neon
status during ordinary authenticated page requests; it must not add a background
database poll. Disable **Check soon** while a nonterminal request exists for the
provider. A failed S3 write should be shown clearly but must not roll back an
already-audited tier decision.

### `/admin/nyc/venues`

Use the same A/B/C/D review model. Show address, area, canonical URL, schedule URLs,
linked offerings, and providers observed there. Support duplicate merge and factual
corrections. A venue tier controls venue-calendar discovery; it does not implicitly
approve every provider or occurrence found there.

### `/admin/nyc/events`

Show offerings and occurrences with provider, teacher, venue, time, registration
URL, extraction method, last confirmation, and evidence. Visually distinguish:

- confirmed upcoming;
- undated recurring offering;
- needs human review;
- sold out or cancelled;
- missing/unconfirmed;
- expired.

Never synthesize dated occurrences from text such as “weekly.” An undated recurring
offering is useful and may remain visible in the private UI, but it is not a dated
calendar occurrence.

### `/admin/nyc/discovery`

List weekly `nyc_discovery_run` rows and drill into every
`nyc_discovery_observation`. Show query, rank, URL, title/snippet, disposition,
linked provider, and rejection reason. The purpose is to answer:

- Which searches find new sources?
- Which query families are redundant or noisy?
- Which boroughs and source types are under-covered?
- Did a run end normally, partially, or fail?

### `/admin/nyc/tasks`

List `nyc_schedule_review` tasks by state and age. Show the source page and bounded
text excerpt only to admins. Allow retrying a failed task or marking it reviewed;
do not run Hermes synchronously inside a web request. A retry changes queue state
for the Mini worker to claim.

## Review transaction

Provider and venue decisions must mirror the Atlas `nyc-review` contract. In one
transaction:

1. Lock the entity row (`SELECT ... FOR UPDATE`).
2. Capture previous status and tier.
3. For A/B/C approval, set status `active`; providers also set `enabled = true`.
4. For D rejection, set status `retired`, tier D; providers set `enabled = false`.
5. Update `crawl_state` keys `tier`, `review_decision`, `reviewed_at`,
   `reviewed_by`, and `review_rationale` without discarding unrelated keys.
6. Insert `nyc_directory_decision` with previous and new values plus actor and
   rationale.
7. Commit, then render the resulting state.

Use optimistic conflict protection in the form submission (the row's prior
`updated_at`). If another writer changed the row, reject the stale form and show
the new value rather than overwriting it.

## Provider and venue edits

Factual edits need their own durable audit trail before the UI enables them. Add a
future `nyc_directory_change` table containing entity type/id, field, previous and
new JSON values, actor, rationale, and timestamp. Until that migration exists,
the first site milestone should make factual fields read-only and implement only
the already-audited A/B/C/D decisions.

Do not edit tracked `config/nyc.yaml` from the web application. Neon is canonical
for operational state. Source-controlled YAML is a reviewed bootstrap/policy
snapshot and may be refreshed deliberately by the Atlas development workflow.

## Operational ownership

- The Mini is the only crawler/enrichment worker.
- The authenticated site is the human control plane.
- Both use the same Neon schema.
- The site never shells into the Mini and never invokes Hermes directly.
- The Mini may immediately observe owner decisions on its next scheduled run.
- The public Atlas build remains disabled until its independent two-key release
  gate is changed by the owner.

## Initial implementation sequence

1. Add a read-only schema adapter and `/admin/nyc` overview.
2. Add provider and venue list/detail pages.
3. Add transactional A/B/C/D review actions and tests for authorization, audit,
   stale-form conflicts, and rollback.
4. Add discovery-run and observation inspection.
5. Add events/evidence and task inspection.
6. Add the `nyc_directory_change` migration, then factual edits and duplicate merge.

## Acceptance criteria

- No NYC route is reachable without admin authentication.
- The site reads and writes only `atlas_nyc` for this feature.
- Every policy mutation is auditable and atomic.
- A candidate approved on the site becomes due to the Mini according to its tier.
- A D record is never crawled again.
- The UI shows weekly search coverage, rejected/noisy results, queue age, and recent
  failures—not only successful listings.
- No action publishes `/nyc/` or changes the public Atlas deployment gate.
