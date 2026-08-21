# Atlas Analysis Projection Handoff

Status: architecture decision for implementation by the main-project agent  
Owner: Michael Hollander  
Date: 2026-08-20

## Decision

The motif/axes dataset consumed by Feldenkrais Atlas is not a GitHub build
artifact and is not generated as part of an Atlas deployment.

It is a durable **public analysis projection** owned and published by the main
Feldenkrais analysis system. After an owner-approved analysis operation has
successfully changed the eligible analyzed dataset, the server rebuilds,
validates, and publishes the projection to private S3 storage.

Publishing the projection is a concluding step of an authorized analysis run.
It does not grant permission to schedule, start, or broaden analysis. Analysis
and the analysis queue remain owner-gated.

## System boundary

```text
Main Feldenkrais database and cached analyses
                    |
                    | owner-approved analysis completes
                    v
       Build and validate public projection
                    |
                    | publish immutable object first
                    v
          Private, versioned S3 projection
                    |
                    | atomically replace current pointer
                    v
        atlas-analysis-current.json
                    |
                    v
        Atlas Lambda/API reads and caches it
                    |
                    v
      Atlas search and chooser game consume it
```

The Atlas application must not query the main analysis database directly.
The main web application does not need to expose the projection through one of
its public routes. S3 is the durable handoff between systems.

## Object naming

Do not use the ambiguous name `latest.json`.

Recommended layout:

```text
atlas-analysis-projections/
  atlas-analysis-current.json
  versions/
    atlas-analysis-2026-08-20T180000Z-<content-sha>.json
```

`versions/atlas-analysis-...json` objects are immutable. The mutable
`atlas-analysis-current.json` object is a small pointer/manifest naming the
currently published immutable projection.

Example pointer:

```json
{
  "schema_version": 1,
  "published_at": "2026-08-20T18:00:00Z",
  "projection_key": "atlas-analysis-projections/versions/atlas-analysis-2026-08-20T180000Z-abc123.json",
  "projection_sha256": "abc123...",
  "lesson_count": 78,
  "analysis_versions": {
    "motifs": {
      "registry": "motifs-v5",
      "prompt": "motifs-p1"
    },
    "axes": {
      "prompt": "axes-p1"
    }
  }
}
```

The exact bucket or isolated prefix may be selected during implementation.
Whichever is chosen must have narrowly scoped IAM permissions for the Mini
publisher and the Atlas reader.

## Projection contents

The projection contains only data approved for public Atlas use. It should be
general enough to carry additional derived measures later without exposing
analysis internals.

Initial record shape:

```json
{
  "schema_version": 1,
  "generated_at": "2026-08-20T18:00:00Z",
  "analysis_versions": {
    "motifs": {
      "registry": "motifs-v5",
      "prompt": "motifs-p1"
    },
    "axes": {
      "prompt": "axes-p1"
    }
  },
  "motifs": [
    {
      "id": "bell_hands",
      "label": "Bell hands",
      "definition": "..."
    }
  ],
  "lessons": [
    {
      "analysis_document_id": "...",
      "atlas_resource_key": "...",
      "title": "...",
      "source_name": "...",
      "language": "en",
      "duration_minutes": 42,
      "is_atm": true,
      "motifs": [
        {
          "id": "bell_hands",
          "mode": "taught",
          "role": "central",
          "certainty": "clear"
        }
      ],
      "axes": {
        "flexion_extension": "mixed_or_balanced",
        "rotation": "moderate",
        "side_bending": "minor",
        "cross_lateral": "central"
      }
    }
  ]
}
```

The projection must exclude:

- transcripts and transcript excerpts;
- raw evidence and step-level reasoning;
- prompts and raw model responses;
- candidate or raw motifs;
- private operational identifiers other than the deliberate join identifier;
- credentials, storage locations, and internal error details.

Only established motifs with `chooser_visible: true` belong in the motif
registry and lesson assignments exported to Atlas. Merged motif stubs must not
appear.

## Cross-system identity

`atlas_resource_key` is the critical integration field. It must let Atlas join
an analyzed document to the corresponding catalog resource without title or
teacher-name matching.

Prefer identifiers in this order:

1. platform plus native item ID, such as a YouTube video ID;
2. normalized canonical media URL;
3. another explicit stable external identifier shared by both systems.

If the exporter cannot produce a confident stable key, it should report the
record as unmatched rather than invent a fuzzy join. The publication summary
must include eligible, matched, unmatched, and exported counts.

## Publication transaction

Publication must be fail-closed and ordered:

1. Read existing cached motif and axes analyses. Do not invoke extraction.
2. Build the projection in memory or a temporary local file.
3. Validate its schema, motif registry version, identifiers, and public-field
   allowlist.
4. Compare it with the currently published manifest.
5. Refuse an empty projection.
6. Refuse or require an explicit override for an unexpectedly large decrease
   in matched/exported lessons.
7. Write an immutable, content-addressed or timestamp-plus-checksum S3 object.
8. Read or HEAD the uploaded object and verify size/checksum.
9. Replace `atlas-analysis-current.json` only after verification succeeds.
10. Print and persist a publication summary suitable for operational audit.

A failure before step 9 leaves the prior current projection active. Old
immutable projections should be retained for rollback and audit, subject to a
later lifecycle policy.

Publishing the same content should be idempotent: it must not create needless
new versions or change the current pointer.

## Refresh semantics

- Projection publication happens after a successful owner-approved analysis
  operation when the eligible public dataset has changed.
- It is not tied to a GitHub Actions build or Atlas deployment.
- It is not a web-request-time operation.
- It is not an unattended trigger for extraction or analysis.
- Registry changes do not silently rebuild analyses. Previously published data
  remains current until the owner authorizes the necessary analysis and a new
  projection is published.

The existing owner-triggered `run_motifs.py index` concept may be used as the
local materialization step, but the final command should explicitly distinguish
building from publishing. For example:

```text
run_motifs.py index ...
run_motifs.py publish-atlas ...
```

The second command reads cached/materialized results only. It must not trigger
LLM work.

## Atlas consumption

Atlas should read `atlas-analysis-current.json`, retrieve the referenced
immutable projection, verify its checksum and schema, and cache the validated
result. A small Lambda/API endpoint may serve the browser-oriented subset to
the Atlas search and game pages.

Requirements:

- retain the last known-good projection if refresh fails;
- use bounded cache lifetimes so a newly published projection appears without
  an Atlas rebuild;
- return a clear unavailable state if no valid projection has ever loaded;
- expose no private S3 URL or credentials to the browser;
- include projection timestamp and analysis versions in the response;
- preserve a stable response schema independently of internal cache-table
  formats.

The browser can run faceting and the chooser game client-side because the
eligible dataset is small. The Lambda is a projection reader and public-data
boundary, not an analysis service.

## Relationship to `MOTIFS_SEARCH_AND_GAME_SPEC.md`

The search and game behavior in `docs/MOTIFS_SEARCH_AND_GAME_SPEC.md` remains
the product specification. This handoff changes its deployment boundary:

- the main project builds and publishes the analysis projection;
- Feldenkrais Atlas owns the public search/game UI;
- the existing Atlas `/browse/` catalog route remains unchanged;
- use a separate Atlas route such as `/finder/` for motif/axes faceting and
  `/game/` for the chooser;
- Atlas results should resolve through `atlas_resource_key` to Atlas resource
  pages rather than assuming the main app's `/lesson/{document_id}` route.

## Implementation sequence

1. Implement and test the cached motif/axes materialized index in the main
   project.
2. Implement the explicit public projection and stable Atlas identity join.
3. Implement validation, regression guards, immutable S3 publication, and the
   `atlas-analysis-current.json` pointer update.
4. Perform a dry run against cached data only and review counts/unmatched rows.
5. With owner approval, publish one projection and verify rollback behavior.
6. Implement the Atlas Lambda reader and public response contract.
7. Integrate the approved chooser prototype into Atlas `/finder/` and implement
   `/game/` against the same response.

## Explicit hold

Do not implement this handoff yet. Do not run analysis, trigger the analysis
queue, publish a projection, or modify AWS resources until the owner gives the
implementation agent further direction.
