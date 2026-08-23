"""Atlas analysis projection: build, validate, publish.

Implements docs/ATLAS_ANALYSIS_PROJECTION_RESPONSE.md (schema_version 1).

Governance: the only owner gate is the analysis pipeline (group gates on
/analysis). Publication runs automatically after an analysis sweep that
completed motifs/axes work, and manually via `run_motifs.py publish-atlas`.
Both paths share the same fail-closed transaction:

  build -> validate -> compare with current pointer -> refuse empty /
  suspicious shrink -> write immutable version object -> verify -> repoint.

Credentials come from ATLAS_PUBLISH_AWS_ACCESS_KEY_ID / _SECRET_ACCESS_KEY
(deliberately not the default AWS_* names). Without them, publish degrades
to a local dry-run file and says so.
"""

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

# Sources whose lessons are approved for public Atlas use. Owner-editable.
PUBLIC_SOURCES = [
    "FELDENKRAIS_PROJECT",
    "FELDENKRAIS_PROJECT_FREE",
    "KINESOPHICS",
    "FELDENKRAIS_ACCESS_YOUTUBE",
]

BUCKET = os.environ.get("ATLAS_PUBLISH_BUCKET", "feldenkrais-prod")
S3_PREFIX = "atlas-analysis-projections/"
POINTER_KEY = S3_PREFIX + "atlas-analysis-current.json"
SCHEMA_VERSION = 1
# Refuse to publish if exported lessons shrink below this fraction of the
# currently published count (without an explicit override).
SHRINK_GUARD = 0.8

_REPO = Path(__file__).resolve().parent.parent.parent
_NON_ATM = re.compile(r"\btalk\b|discussion|interview", re.I)


def _feldenkrais_project_public_urls() -> list[str]:
    """Return the owner-verified free Feldenkrais Project lesson allowlist."""
    path = _REPO / "data" / "fp_free_lessons.json"
    return [row["url"] for row in json.loads(path.read_text())]


def _load_registry() -> dict:
    for base in (_REPO / "private" / "data", _REPO / "data"):
        p = base / "motifs_registry.yaml"
        if p.exists():
            return yaml.safe_load(p.read_text())
    raise FileNotFoundError("motifs_registry.yaml not found")


def _prompt_versions() -> tuple[str, str]:
    from analysis.motifs import PROMPT_VERSION
    from analysis.axes import AXES_PROMPT_VERSION
    return PROMPT_VERSION, AXES_PROMPT_VERSION


def _lite_prompt_versions() -> tuple[str, str, str]:
    from analysis.motifs import LITE_PROMPT_VERSION as motifs_lite
    from analysis.axes import LITE_PROMPT_VERSION as axes_lite
    from analysis.lesson_card import PROMPT_VERSION as card
    return motifs_lite, axes_lite, card


def _cache_key(text: str, suffix: str) -> str:
    return hashlib.sha256((text.strip() + suffix).encode()).hexdigest()[:16]


def _registry_sha(registry_version: str, motifs: list[dict]) -> str:
    return hashlib.sha256(json.dumps(
        {"registry_version": registry_version, "motifs": motifs},
        sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()


def build_projection(db) -> tuple[dict, dict]:
    """Build the projection from cached analyses only (never invokes LLMs).

    Returns (projection, summary). Summary counts: eligible (transcribed in
    allowlisted sources), exported, missing_analysis.
    """
    registry = _load_registry()
    ver = registry.get("version", "motifs-v0")
    motif_prompt, axes_prompt = _prompt_versions()
    motifs_lite_prompt, axes_lite_prompt, card_prompt = _lite_prompt_versions()
    vis = sorted(
        ({"id": m["id"], "label": m["label"], "definition": m["definition"].strip()}
         for m in registry.get("motifs", [])
         if m.get("status") == "established" and m.get("chooser_visible")),
        key=lambda m: m["id"])
    vis_ids = {m["id"] for m in vis}

    with db.get_cursor(commit=False) as cur:
        cur.execute("""
            SELECT d.id, d.title, d.source_url, ds.name AS source_name,
                   a.data->>'text' AS text,
                   (a.data->'segments'->-1->>'end')::float AS end_s,
                   COALESCE(d.metadata->>'lesson_type',
                            d.metadata->'lesson_metadata'->>'lesson_type') AS lesson_type
            FROM document d
            JOIN document_source ds ON ds.id = d.source_id
            JOIN analysis a ON a.document_id = d.id AND a.type_id = 1
            WHERE (ds.name = ANY(%s) OR d.origin = 'feldenkrais_atlas')
              AND (ds.name <> 'FELDENKRAIS_PROJECT' OR d.source_url = ANY(%s))
            ORDER BY (d.origin = 'feldenkrais_atlas') DESC NULLS LAST, ds.name, d.title
        """, (PUBLIC_SOURCES, _feldenkrais_project_public_urls()))
        rows = [dict(r) for r in cur.fetchall()]

    keyed_rows = []
    wanted_keys = []
    for row in rows:
        text = row["text"] or ""
        if not text.strip():
            keyed_rows.append((row, None))
            continue
        keys = {
            "motifs": _cache_key(text, f":distinctive_motifs_lite:{ver}:{motifs_lite_prompt}"),
            "axes": _cache_key(text, f":movement_axes_lite:{axes_lite_prompt}"),
            "card": _cache_key(text, f":lesson_card:{card_prompt}"),
        }
        wanted_keys.extend(keys.values())
        keyed_rows.append((row, keys))
    with db.get_cursor(commit=False) as cur:
        cur.execute("SELECT key, data FROM cache WHERE key = ANY(%s)", (wanted_keys,))
        cached = {row["key"]: row["data"] for row in cur.fetchall()}

    lessons, missing, excluded_unrelated = [], 0, 0
    for r, keys in keyed_rows:
        text = r["text"] or ""
        if not text.strip() or keys is None:
            missing += 1
            continue
        # Lite enrichment is the independent Atlas data contract. Full analysis
        # can later overlay these fields without changing search or game code.
        mrow = cached.get(keys["motifs"])
        arow = cached.get(keys["axes"])
        card = cached.get(keys["card"])
        if not mrow or not arow or not card:
            missing += 1
            continue
        if card.get("is_feldenkrais_related") is False:
            excluded_unrelated += 1
            continue
        title = (r["title"] or "").replace(" - Feldenkrais Project", "")
        lt = (card.get("lesson_type") or r.get("lesson_type") or "").lower()
        is_atm = not _NON_ATM.search(title) and lt in ("", "atm", "unknown")
        lessons.append({
            "analysis_document_id": str(r["id"]),
            "atlas_resource_key": None,  # populated when the join lands
            "canonical_source_url": r["source_url"],
            "title": title,
            "source_name": r["source_name"],
            "language": card.get("primary_language"),
            "duration_minutes": round(r["end_s"] / 60) if r.get("end_s") else None,
            "is_atm": is_atm,
            "display": {
                field: card.get(field)
                for field in (
                    "summary", "suggested_title", "is_feldenkrais_related",
                    "feldenkrais_relevance_confidence", "relevance_reason",
                    "lesson_type", "starting_position", "primary_positions",
                    "positions", "themes", "support_context", "equipment",
                    "primary_language", "notable_feature", "audience_notes",
                )
            },
            "provenance": {"axes": "gemini_lite", "motifs": "gemini_lite",
                           "display": "gemini_lite"},
            "motifs": [
                {"id": a["motif_id"], "mode": a["mode"],
                 "role": a["role"], "certainty": a["certainty"]}
                for a in mrow.get("assignments", []) if a["motif_id"] in vis_ids
            ],
            "axes": {ax: v["value"] for ax, v in arow.get("axes", {}).items()},
        })

    projection = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "analysis_versions": {
            "motifs": {"registry": ver,
                       "registry_sha256": _registry_sha(ver, vis),
                       "prompt": motif_prompt},
            "axes": {"prompt": axes_prompt},
            "lite": {"motifs_prompt": motifs_lite_prompt,
                     "axes_prompt": axes_lite_prompt, "card_prompt": card_prompt},
        },
        "motifs": vis,
        "lessons": lessons,
    }
    summary = {"eligible": len(rows), "exported": len(lessons),
               "missing_analysis": missing,
               "excluded_unrelated": excluded_unrelated,
               "registry_version": ver}
    return projection, summary


_AXIS_VALUES = {
    "flexion_extension": {"strongly_flexion", "flexion_leaning", "mixed_or_balanced",
                          "extension_leaning", "strongly_extension"},
    "rotation": {"none", "minor", "moderate", "central"},
    "side_bending": {"none", "minor", "moderate", "central"},
    "cross_lateral": {"none", "minor", "moderate", "central"},
    "vigor": {"meditative", "gentle", "moderate", "vigorous"},
}
_FORBIDDEN_LESSON_KEYS = {"evidence", "candidate_motifs", "raw_motifs", "transcript", "text"}
_LESSON_TYPES = {"atm", "talk", "interview", "demonstration", "mixed", "other"}
_POSITIONS = {"lying_on_back", "lying_on_side", "lying_on_front", "sitting", "standing", "kneeling", "all_fours", "other"}
_SUPPORT_CONTEXTS = {"floor", "chair", "standing", "wall"}


def validate_projection(proj: dict) -> list[str]:
    errors = []
    if proj.get("schema_version") != SCHEMA_VERSION:
        errors.append("bad schema_version")
    vis_ids = {m["id"] for m in proj.get("motifs", [])}
    if not proj.get("lessons"):
        errors.append("empty projection")
    for l in proj.get("lessons", []):
        if set(l) & _FORBIDDEN_LESSON_KEYS:
            errors.append(f"forbidden field in lesson {l.get('analysis_document_id')}")
        for m in l.get("motifs", []):
            if m["id"] not in vis_ids:
                errors.append(f"non-exported motif id {m['id']}")
        for ax, v in l.get("axes", {}).items():
            if ax not in _AXIS_VALUES or v not in _AXIS_VALUES[ax]:
                errors.append(f"bad axis {ax}={v}")
        display = l.get("display") or {}
        if not isinstance(display.get("is_feldenkrais_related"), bool):
            errors.append("bad is_feldenkrais_related")
        confidence = display.get("feldenkrais_relevance_confidence")
        if (
            not isinstance(confidence, (int, float))
            or isinstance(confidence, bool)
            or not 0 <= confidence <= 1
        ):
            errors.append("bad feldenkrais_relevance_confidence")
        if not str(display.get("relevance_reason") or "").strip():
            errors.append("missing relevance_reason")
        if display.get("lesson_type") not in _LESSON_TYPES:
            errors.append(f"bad lesson_type {display.get('lesson_type')}")
        if display.get("starting_position") not in _POSITIONS | {None}:
            errors.append(f"bad starting_position {display.get('starting_position')}")
        if not set(display.get("positions") or []) <= _POSITIONS:
            errors.append("bad positions")
        if not set(display.get("primary_positions") or []) <= _POSITIONS:
            errors.append("bad primary_positions")
        if len(display.get("primary_positions") or []) > 3:
            errors.append("too many primary_positions")
        if not set(display.get("support_context") or []) <= _SUPPORT_CONTEXTS:
            errors.append("bad support_context")
        provenance = l.get("provenance") or {}
        if any(value != "gemini_lite" for value in provenance.values()):
            errors.append("bad lite provenance")
    return errors


def _s3_client():
    key = os.environ.get("ATLAS_PUBLISH_AWS_ACCESS_KEY_ID")
    secret = os.environ.get("ATLAS_PUBLISH_AWS_SECRET_ACCESS_KEY")
    if not key or not secret:
        return None
    import boto3
    return boto3.client(
        "s3", aws_access_key_id=key, aws_secret_access_key=secret,
        region_name=os.environ.get("ATLAS_PUBLISH_AWS_REGION", "us-east-1"))


LAST_PUBLICATION_KEY = "atlas:last_publication"


def _finish(db, result: dict) -> dict:
    """Persist the outcome for the /analysis dashboard panel (best-effort)."""
    try:
        db.set_cache(LAST_PUBLICATION_KEY, {
            **{k: v for k, v in result.items() if k != "errors" or result[k]},
            "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
    except Exception:
        pass
    return result


def publish(db, dry_run: bool = False, out: str | None = None,
            force_decrease: bool = False) -> dict:
    """Fail-closed publication. Returns an auditable summary dict."""
    projection, summary = build_projection(db)
    errors = validate_projection(projection)
    if errors:
        return _finish(db, {**summary, "status": "invalid", "errors": errors[:10]})

    body = json.dumps(projection, separators=(",", ":")).encode()
    sha = hashlib.sha256(body).hexdigest()
    summary["projection_sha256"] = sha

    if out:
        Path(out).write_bytes(body)
        summary["local_file"] = out

    s3 = _s3_client()
    if dry_run or s3 is None:
        summary["status"] = "dry-run" if dry_run else "dry-run (no ATLAS_PUBLISH credentials)"
        return _finish(db, summary)

    # Compare with current pointer: idempotency + shrink guard.
    current = None
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=POINTER_KEY)
        current = json.loads(obj["Body"].read())
    except s3.exceptions.NoSuchKey:
        pass
    except Exception as e:  # first publish: bucket reachable but no pointer
        if "NoSuchKey" not in str(e) and "404" not in str(e):
            return _finish(db, {**summary, "status": "error", "error": f"pointer read: {e}"})
    if current:
        if current.get("projection_sha256") == sha:
            return _finish(db, {**summary, "status": "unchanged", "published": False})
        prev_n = current.get("lesson_count") or 0
        if prev_n and len(projection["lessons"]) < SHRINK_GUARD * prev_n and not force_decrease:
            return _finish(db, {**summary, "status": "refused-shrink",
                    "previous_lesson_count": prev_n,
                    "hint": "re-run with --force-decrease if intentional"})

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    version_key = f"{S3_PREFIX}versions/atlas-analysis-{ts}-{sha[:12]}.json"
    s3.put_object(Bucket=BUCKET, Key=version_key, Body=body,
                  ContentType="application/json")
    head = s3.head_object(Bucket=BUCKET, Key=version_key)
    if head["ContentLength"] != len(body):
        return _finish(db, {**summary, "status": "error", "error": "size mismatch after upload"})

    pointer = {
        "schema_version": SCHEMA_VERSION,
        "published_at": projection["generated_at"],
        "projection_key": version_key,
        "projection_sha256": sha,
        "lesson_count": len(projection["lessons"]),
        "analysis_versions": projection["analysis_versions"],
    }
    s3.put_object(Bucket=BUCKET, Key=POINTER_KEY,
                  Body=json.dumps(pointer, indent=1).encode(),
                  ContentType="application/json")
    return _finish(db, {**summary, "status": "published", "published": True,
            "projection_key": version_key,
            "previous": current.get("projection_key") if current else None})


PROJECTION_STEPS = {"motifs", "axes"}


def maybe_publish_after_sweep(db, completed_step_names) -> dict | None:
    """Auto-publish hook for the analysis workers. Fail-soft: never raises.

    Publishes only when the sweep completed steps that change the projection.
    The analysis gates are the sole owner control; this runs unattended.
    """
    if not PROJECTION_STEPS & set(completed_step_names):
        return None
    try:
        result = publish(db)
        print(f"[atlas-projection] {json.dumps(result, default=str)}", flush=True)
        return result
    except Exception as e:
        print(f"[atlas-projection] publish failed (non-fatal): {e}", flush=True)
        return {"status": "error", "error": str(e)}
