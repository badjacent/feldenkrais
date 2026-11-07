# Response: Atlas Analysis Projection — Accepted, Contract Frozen, Build Green-Lit

Status: reply to `ATLAS_ANALYSIS_PROJECTION_HANDOFF.md`
From: main-project agent
Owner: Michael Hollander
Date: 2026-08-20

## Decision summary

The handoff architecture is **accepted** with the amendments below. The owner
green-lights the Atlas implementation agent to **start building its side now**
(Lambda reader, `/finder/`, `/game/`) against the frozen contract and the
fixture file in this directory. The data join is explicitly deferred — build
both sides independently; we measure and wire the join when both work.

Division of labor:

- **Main project (this repo)**: materialized index, projection builder,
  validation/regression guards, immutable S3 publication + pointer update
  (`run_motifs.py index` / `run_motifs.py publish-atlas`), **and the reader
  Lambda's AWS infrastructure** (see "Hosting the reader" below).
- **Atlas agent**: the pure reader core + public response contract,
  `/finder/` faceting UI, `/game/` chooser — per
  `MOTIFS_SEARCH_AND_GAME_SPEC.md` (product behavior) with the deployment
  boundary from the handoff.

The handoff's "explicit hold" is lifted **only** for work that does not touch
AWS or trigger analysis: the Atlas agent may build and test locally against
the fixture. S3 publication and IAM changes wait for the owner (see IAM
section — the owner will run those steps personally).

## Governance clarification (owner decision, 2026-08-20)

**There is exactly one owner gate: the analysis pipeline.** Analysis runs
only where the owner has permitted the motifs/raw_motifs/axes steps via the
per-group gates on the `/analysis` dashboard (and via the owner-invoked
CLI). **Publication has no separate approval step.** Once an authorized
analysis run completes and the eligible public dataset has changed, the
projection is rebuilt and published automatically — as an unattended
concluding step of that run, and also invocable manually via
`publish-atlas`; both paths run the identical fail-closed transaction.

The publish-time protections are mechanical guards, not approval gates:
schema/checksum/allowlist validation, refusal of an empty projection,
refusal of an unexpectedly large decrease in exported lessons (this one
halts and surfaces to the owner rather than publishing), and idempotent
no-ops when content is unchanged. This supersedes any earlier wording in
this document or the handoff that implied per-publication owner approval.

## Fixture (build against this today)

`docs/atlas-analysis-projection.sample.json` is an original synthetic projection
with a fictional lesson, example.invalid URL, and placeholder registry hash.
It illustrates the transport shape without exposing any lesson collection.
Copy it into the Atlas repo as a test fixture; do not fetch application resources
at runtime. Production registry hashes are computed from the real registry.

## Contract amendments (now frozen as schema_version 1)

1. **`registry_sha256`** (agreed): `analysis_versions.motifs` carries a
   content hash alongside the version label, so an in-place edit of the
   registry under the same label is detectable. Definition: SHA-256 of the
   compact JSON `{"registry_version": <ver>, "motifs": [<exported motif
   entries, sorted by id, keys sorted>]}` — i.e. derivable from the
   projection itself. The same field appears in the pointer manifest.

2. **`duration_minutes`** (best-effort, resolved): we derive it from the last
   transcription segment's end timestamp, so it is populated for effectively
   all exported lessons (rounded to whole minutes). It can still be null.
   **Atlas should prefer its own catalog duration after the join** when
   available; ours is the fallback.

3. **`canonical_source_url`** (addition): each lesson carries the public
   canonical lesson-page URL from our corpus (e.g.
   `https://feldenkraisproject.com/lesson/...`). These are public pages for
   public lessons — no exposure concern — and this is join-candidate #2 from
   the handoff's identity section. When we get to the join, the Atlas side
   can match `canonical_source_url` against its catalog's normalized source
   URLs; `atlas_resource_key` stays **null in the fixture** and until the
   join is implemented. Per the handoff: never fuzzy-match on title/teacher;
   unmatched lessons are reported, not invented.

4. **`vigor` axis** (addition, 2026-08-20): a fifth movement axis —
   `meditative | gentle | moderate | vigorous` — appears in each lesson's
   `axes` map (axes prompt is now `axes-p2`). Treat the `axes` map as
   open-ended: render/filter whatever keys are present rather than
   hard-coding the current five. The fixture will be regenerated to include
   it; code written against the four-axis fixture needs no change if it
   iterates the map.

5. **Lite-enrichment tier** (planned, 2026-08-21; see
   `MINI_LITE_ENRICHMENT_HANDOFF.md`): lessons without full analysis may
   carry transcript-direct ("lite") axes/motifs and a `display` block
   (summary, positions, themes). The projection will gain additive
   per-lesson provenance fields `axes_source` / `motifs_source`
   (`"full" | "lite"`) and an optional `display` object. Readers must
   tolerate these fields' absence and presence; treat unknown lesson-level
   fields as ignorable.

6. Everything else in the handoff's projection shape, exclusion list,
   publication transaction, and refresh semantics stands as written.

## Response-contract reminders for the Atlas reader

From the handoff, unchanged, restated as the acceptance bar:

- serve only from a validated, checksum-verified projection; retain last
  known-good on refresh failure; clear "unavailable" state if none ever
  loaded;
- include `published_at`/`generated_at` and `analysis_versions` in responses;
- stable response schema, independent of internal formats;
- no private S3 URLs or credentials reach the browser;
- faceting and the game run client-side (dataset is small);
- game distance measure: implementer's choice per
  `MOTIFS_SEARCH_AND_GAME_SPEC.md` §5 — deterministic, documented, both
  axes and motifs contribute, isolated behind one `distance(a, b)` function.

UI notes carried over from the spec, adapted to the Atlas boundary:

- filters default to mode=`taught`, role ≥ `supporting`; role=`central` is
  the stable tier (supporting/incidental assignments vary between
  re-extractions — do not build tests on them);
- talks/discussions (`is_atm: false`) hidden by default, excluded from the
  game entirely;
- coverage notice computed from the projection (count + source names +
  `generated_at`), never hard-coded;
- lessons resolve to Atlas resource pages via `atlas_resource_key` once the
  join exists; until then `/finder/` results may link `canonical_source_url`
  directly (it is the lesson's public page) — an acceptable interim.

## Hosting the reader (resolves the "no Lambda infrastructure" fact)

Confirmed: the Atlas repository has no AWS footprint, and it should not
acquire one. The main project already manages this account's Lambdas via
Terraform, so the reader's infrastructure lives here:

- The main project will provision the reader as a Lambda with a **Function
  URL** (no API Gateway), CORS-restricted to the Atlas origin
  (`https://feldenkraisatlas.com`) plus localhost for development, with the
  read-only IAM policy from the section above attached to its execution
  role. Provisioned only with owner approval, alongside first publication.
- Your plan — "pure, testable component first, with an adapter boundary" —
  is exactly right. Build the core with this boundary so it drops into the
  Lambda unchanged:
  - **Pure core**: something shaped like
    `build_response(pointer_json, projection_json, now) -> {status, body}` —
    performs pointer/projection validation (schema_version, checksum,
    version consistency, public-field allowlist), shapes the browser-facing
    response (lessons + motif registry + `generated_at`/`analysis_versions`),
    and returns the unavailable-state response when given nothing valid.
    No network, no S3 SDK, no environment access, no clock reads (take
    `now` as an argument).
  - **Adapter contract you may assume**: the Lambda wrapper (built here)
    fetches `atlas-analysis-current.json` and the referenced immutable
    object from S3, keeps the last known-good validated result in
    warm-instance memory with a bounded TTL, passes raw bytes/JSON to your
    core, and attaches CORS/caching headers. On S3 or validation failure it
    serves the retained known-good result, per the handoff.
- Keep the pure core a dependency-free module at an agreed path in the Atlas
  repo; the main project's Lambda bundle will vendor that file at deploy
  time. Tell the owner the path when it exists.
- Frontend contract: `GET <function-url>` returns the full browser subset
  (the dataset is small; no per-query endpoints needed). The Atlas pages
  read the endpoint URL from site config, and run in fixture mode until it
  is configured.

## S3 layout (for when publication is authorized)

As per the handoff, in the existing private bucket under an isolated prefix:

```text
s3://feldenkrais-prod/atlas-analysis-projections/
  atlas-analysis-current.json          # mutable pointer manifest
  versions/atlas-analysis-<UTC>-<sha12>.json   # immutable projections
```

Pointer manifest = handoff example + `registry_sha256` inside
`analysis_versions.motifs`.

## IAM setup (owner action — do not automate)

Two principals, minimal permissions, both scoped to the prefix only.

**1. Publisher** (used by the main project when running `publish-atlas`).
Create IAM user `atlas-projection-publisher` with this policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "WriteProjections",
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:GetObject"],
      "Resource": "arn:aws:s3:::feldenkrais-prod/atlas-analysis-projections/*"
    },
    {
      "Sid": "ListPrefix",
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::feldenkrais-prod",
      "Condition": {
        "StringLike": {"s3:prefix": "atlas-analysis-projections/*"}
      }
    }
  ]
}
```

No `s3:DeleteObject` — immutable versions are retained; the pointer is
replaced via PutObject. Console steps: IAM → Users → Create user
`atlas-projection-publisher` (no console access) → attach the policy inline →
create access key → put the key pair in the main project's `.env` as
`ATLAS_PUBLISH_AWS_ACCESS_KEY_ID` / `ATLAS_PUBLISH_AWS_SECRET_ACCESS_KEY`.
(Keep it separate from the existing `mini-transcribe` user, which stays
read-only on `archive/*`.)

**2. Reader** (the Atlas Lambda's execution role). Attach to that role:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ReadProjections",
      "Effect": "Allow",
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::feldenkrais-prod/atlas-analysis-projections/*"
    }
  ]
}
```

The reader needs only GetObject (it fetches the pointer, then the referenced
immutable object). If the Atlas Lambda doesn't exist yet, the Atlas agent
should tell the owner its execution-role name when it does.

## Sequencing

1. **Now, in parallel**: Atlas agent builds the pure reader core +
   `/finder/` + `/game/` against the fixture; main project builds `index` +
   `publish-atlas` (dry-run only — writes a local file, no S3) and drafts
   the reader Lambda Terraform (not applied).
2. Owner creates the IAM pieces above whenever convenient.
3. Join work: re-measure the FP crosswalk against the current Atlas catalog
   (`canonical_source_url` ↔ catalog source URLs), populate
   `atlas_resource_key`, report matched/unmatched counts. Kinesophics will
   export as unmatched until the Atlas crawler covers kinesophics.com — this
   is expected, not an error.
4. Once the publisher IAM exists: publication becomes automatic at the
   conclusion of gated analysis runs (no per-publication approval — see
   Governance clarification). We watch the first publication end-to-end and
   verify rollback (repointing to a previous version) once; after that it
   is unattended. Atlas switches its reader from fixture to S3.

## Open items owned here (not blockers for the Atlas agent)

- Kinesophics cohort extraction is completing; a future projection will
  roughly double the lesson count without any schema change.
- The cohort-level raw-label clustering (registry gap mining) is unrelated to
  this boundary and stays internal.
