# Atlas motif review gate

Status: proposed design, 2026-08-22

## Decision

Gemini may nominate closed-set motif assignments, but no assignment is eligible
for a public Atlas filter until a human approves it. LLM confidence, evidence,
and assignment role help order the review queue; they do not substitute for the
human decision.

The public rule is therefore:

```text
currently nominated as taught + central/supporting + not uncertain
AND human decision for this transcript and motif definition = approved
```

An unreviewed, deferred, or rejected assignment remains available for internal
audit but is absent from public search, cards, and similarity inputs that are
described as curated.

## Reviewer experience

Add a **Motif review** section to the Tailscale-only Mini dashboard. Nothing on
the public site links to it.

The default view is a queue of pending assignments. Each review card contains:

- lesson title, source, duration, and a link to the original recording;
- the proposed motif label;
- the registry definition plus its include/exclude rules and counterexamples;
- Gemini's mode, role, certainty, reason, and exact evidence quotations;
- enough transcript around each quotation to judge it in context;
- an expandable full transcript;
- the extraction model, prompt version, registry version, and extraction time.

The actions are **Approve**, **Reject**, and **Defer**. Approve and reject may
accept an optional note. Keyboard shortcuts can make a later high-volume pass
fast, but the first implementation should optimize for legibility rather than
bulk approval.

The page also has Approved and Rejected tabs, source and motif filters, and a
search box. Every previous decision can be reopened and changed. The header
shows pending, approved, rejected, and public-eligible counts so it is obvious
whether review is blocking a release.

## Persistence

Store review state in Neon, not in a browser, cache file, or the Mini's SQLite
database. It is editorial state and must survive replacement of the Mini.

Suggested table:

```sql
CREATE TABLE atlas_motif_review (
    document_id uuid NOT NULL,
    motif_id text NOT NULL,
    transcript_sha256 text NOT NULL,
    motif_definition_sha256 text NOT NULL,
    decision text NOT NULL CHECK (decision IN ('approved', 'rejected', 'deferred')),
    reviewer text,
    note text,
    decided_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (
        document_id, motif_id, transcript_sha256, motif_definition_sha256
    )
);
```

The decision identity deliberately excludes model and prompt versions. A human
is approving whether a motif is present in a particular transcript under a
particular registry definition, not approving one model run. Re-enrichment can
improve evidence without erasing valid editorial work.

A changed transcript or changed motif definition creates a new pending review.
A change in Gemini's current assignment can still remove an old approval from
the public output: the current nomination must continue to satisfy the machine
eligibility rule. If it later becomes eligible again against the same transcript
and definition, the existing human approval applies.

Reviewer identity can initially be a configured constant because access is
already restricted to the Tailnet. The schema keeps it explicit so stronger
identity can be added later.

## Queue materialization

The queue is computed from the latest Lite movement artifact joined to review
decisions. It need not be a second durable task queue. A candidate is pending
when it passes the machine eligibility rule and has no matching approved or
rejected decision. Deferred items remain visible in a separate deferred group.

The dashboard should show transcript context generated deterministically around
each exact evidence quotation. Failure to find a quotation verbatim is a visible
warning and should move the item to the top of the queue; it must never be hidden
or treated as approval.

Rejected assignments remain audit-visible. This lets later prompt experiments
measure false-positive rates by motif and identify definitions that repeatedly
need correction.

## Local service API

Extend the existing Tailscale-only dashboard service with a small authenticated-
by-network API:

```text
GET  /api/motif-reviews?state=pending&motif=&source=&q=
POST /api/motif-reviews/:document_id/:motif_id
     {"decision":"approved|rejected|deferred","note":"..."}
```

The POST endpoint resolves the transcript and current registry definition on the
server and writes their hashes; the browser does not supply identity hashes.
Use a CSRF token and accept requests only from the dashboard origin. Continue to
bind the service only to its Tailscale address; do not expose this API through
the public Atlas deployment.

## Projection and publishing

The analysis projection retains all nominated motifs for private audit, with a
review block containing the decision and decision timestamp. It also emits a
separate public-safe list, for example `public_motif_ids`, containing only
currently eligible and human-approved motifs.

The Atlas importer must build public finder fields from `public_motif_ids`, never
from raw assignments. Validation fails closed if review information is absent or
malformed: the affected motif is omitted, while the lesson itself may still be
published.

Applications use a different policy. Per the prompt experiment judgment, only
applications classified as `intended` are public; they do not enter this motif
review queue unless a later owner decision adds an application review gate.

## Telemetry

Add these dashboard and operational metrics:

- current eligible candidates, pending reviews, approved-public, rejected, and
  deferred;
- decisions in the last hour and 24 hours;
- median age and oldest age of pending reviews;
- pending counts by source and motif;
- rejection rate by motif and prompt version;
- evidence quotations not found verbatim in the transcript;
- approved assignments suppressed because the latest extraction no longer
  passes the machine eligibility rule.

These measurements make prompt quality reviewable without letting prompt changes
silently alter the public taxonomy.

## Implementation order

1. Add the Neon migration and read/write repository functions.
2. Materialize the pending queue from current Lite motif artifacts.
3. Add the dashboard review UI and local API, including transcript context.
4. Add review state and `public_motif_ids` to the projection.
5. Change the Atlas importer/finder to consume only `public_motif_ids`.
6. Add fail-closed contract tests and an end-to-end approve/reject/re-enrich test.
7. Populate the queue, review an initial sample, and only then enable the motif
   filters publicly.
