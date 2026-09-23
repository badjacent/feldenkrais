"""A lesson and its analysis as one Markdown document to paste into an LLM.

The reader is an LLM that knows the Feldenkrais Method but nothing about this
analyzer. The document therefore explains itself: what it is, how far each part
can be trusted, how the analysis was produced, and what every field means. Field
definitions come from the analyzer's own output schemas, so they stay in step
with the prompts that produce the fields.

``build_lesson_context`` is pure: callers load the document, transcript and one
analysis profile's results, and pass them in.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import conversation_import

# SPIFFER-Q step tags, as defined for the decomposition prompt
# (data/frameworks/v1/pass_a.md, "SPIFFER-Q Decomposition").
SPIFFER_Q = [
    ("S", "Sensation", "mapping pressures, contours, weight distribution"),
    ("P", "Pause", "integration periods, rest"),
    ("I", "Imagery", "metaphors, analogies, mental geometry"),
    ("F", "Function", "global goals such as walking, reaching, standing"),
    ("F", "Focus", "directing attention to specific anatomy"),
    ("E", "Effort", "instructions to reduce tone, soften, release"),
    ("R", "Relationships", "connections between body parts (head–pelvis, ribs–hips)"),
    ("Q", "The Question", "the neurological riddle the step poses"),
]

SOMATIC_FIELDS = [
    "clarified", "intent", "mechanical_directive", "attentional_cue", "sensory_inquiry",
    "differentiation", "activation", "inhibition", "transmission_route", "ground_push",
]
LANGUAGE_FIELDS = [
    "attention_cues", "effort_language", "image_language", "explanatory_language",
    "distinctive_phrasing",
]
CARD_FIELDS = ["the_action", "the_body", "the_attention", "the_intention", "the_voice"]
SETTING_FIELDS = ["somatic_intent", "space", "teacher_presence", "arc"]

_PLACEHOLDERS = {"", "none", "n/a", "na", "not applicable", "null", "-", "—"}
PARAGRAPH_SECONDS = 60


def _blank(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().strip(".").lower() in _PLACEHOLDERS or value.strip().lower().startswith("none —")
    if isinstance(value, (list, dict)):
        return not value
    return False


def _text(value) -> str:
    if isinstance(value, list):
        return "; ".join(str(v) for v in value if not _blank(v))
    return str(value).strip()


def _label(field: str) -> str:
    return field.replace("the_", "").replace("_", " ")


def _clock(seconds) -> str:
    seconds = int(seconds or 0)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def schema_field_descriptions(schema_dir: Path) -> dict[str, str]:
    """Map field name -> description, gathered from the analysis output schemas."""
    descriptions: dict[str, str] = {}

    def walk(node):
        if isinstance(node, dict):
            for name, sub in (node.get("properties") or {}).items():
                if isinstance(sub, dict) and sub.get("description") and name not in descriptions:
                    descriptions[name] = sub["description"]
                walk(sub)
            for key in ("items",):
                if isinstance(node.get(key), dict):
                    walk(node[key])

    for name in ("pass_a", "pass_0b", "pass_0b_lang", "pass_0b_condensed",
                 "pass_0b_concepts", "pass_0b_spatial_v3"):
        path = Path(schema_dir) / f"{name}.schema.json"
        if path.exists():
            walk(json.loads(path.read_text()))
    return descriptions


def _step_times(pass_a: dict, whisper_segments: list | None) -> dict[int, tuple[float, float]]:
    """Step number -> (start, end) seconds, when the transcript has timings."""
    pass_0 = (pass_a or {}).get("pass_0") or {}
    segments = {s.get("id"): s for s in pass_0.get("segments", [])}
    times = {}
    for step in (pass_a or {}).get("steps", []):
        ids = step.get("segments") or list(range(step.get("segment_start") or 0, (step.get("segment_end") or -1) + 1))
        starts = [segments[i]["time_start"] for i in ids if i in segments and segments[i].get("time_start") is not None]
        ends = [segments[i].get("time_end", segments[i]["time_start"]) for i in ids
                if i in segments and segments[i].get("time_start") is not None]
        if starts:
            times[step["step_number"]] = (min(starts), max(ends))
    return times


def _transcript(text: str, whisper_segments: list | None) -> str:
    """The transcript in paragraphs, each opening with its timestamp when known."""
    if not whisper_segments:
        return (text or "").strip()
    paragraphs, current, started = [], [], None
    for seg in whisper_segments:
        start = seg.get("start")
        if started is None:
            started = start or 0
        current.append((seg.get("text") or "").strip())
        if start is not None and start - started >= PARAGRAPH_SECONDS:
            paragraphs.append(f"[{_clock(started)}] " + " ".join(p for p in current if p))
            current, started = [], None
    if current:
        paragraphs.append(f"[{_clock(started)}] " + " ".join(p for p in current if p))
    return "\n\n".join(paragraphs)


def slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (title or "lesson").lower()).strip("-")[:80] or "lesson"


def build_lesson_context(
    *,
    document: dict,
    source_name: str,
    lesson_url: str,
    profile: dict,
    transcript: str,
    whisper_segments: list | None,
    pass_a: dict | None,
    somatic: dict | None,
    language: dict | None,
    condensed: dict | None,
    concepts: dict | None,
    positions: dict | None,
    field_descriptions: dict[str, str],
    exported_at: str | None = None,
    saving: dict | None = None,
) -> str:
    """Return the full context document for one lesson under one analysis profile.

    ``saving`` describes how to send the conversation back: ``endpoint`` (the
    REST import URL), ``token`` and ``expires`` (the import token and its expiry
    date), and optionally ``bucket`` (the S3 drop point). Without it the
    document has no saving section.
    """
    title = document.get("title") or "Untitled lesson"
    model = f"{profile['model']} (analysis profile `{profile['key']}`)"
    out: list[str] = [f"# {title}", ""]

    meta = [f"- Source: {source_name}" if source_name else None]
    if document.get("source_url") and str(document["source_url"]).startswith("http"):
        meta.append(f"- Original: {document['source_url']}")
    if document.get("lesson_date"):
        meta.append(f"- Date: {document['lesson_date']}")
    if whisper_segments:
        meta.append(f"- Length: about {_clock(whisper_segments[-1].get('end') or 0)}")
    meta.append(f"- Lesson page: {lesson_url}")
    meta.append(f"- Analysis by: {model}")
    if exported_at:
        meta.append(f"- Reference: lesson {document.get('id')} · profile {profile['key']} · exported {exported_at}")
    out += [m for m in meta if m] + [""]

    if whisper_segments:
        primary = ("**The transcript** (last section) is the lesson as taught: a speech-to-text "
                   "transcript of the recording, with timestamps. It is the primary source. It can "
                   "contain transcription errors, and it records the teacher's words, not what "
                   "students did.")
    else:
        primary = ("**The lesson text** (last section) is the primary source: the lesson as it was "
                   "imported from its source, which may be a book or written notes rather than a "
                   "recording. It records the teacher's instructions, not what students did.")
    out += [
        "## About this document",
        "",
        "This is one Feldenkrais lesson, prepared so we can discuss it. It has two kinds of material.",
        "",
        f"- {primary}",
        f"- **Everything else is machine analysis.** {profile['model']} produced it from that text, "
        "through a fixed series of prompts in a lesson-analysis tool. It is interpretation, not ground "
        "truth: it can misread, over-read, or invent. When the analysis and the source text disagree, "
        "the source text wins. Treat anatomical claims in particular as hypotheses.",
        "",
        "Step and group numbers tie the analysis sections together"
        + ("; timestamps on steps point into the transcript." if whisper_segments else "."),
        "",
    ]

    # ── How the analysis was made ──────────────────────────────────────────
    source_word = "transcript" if whisper_segments else "lesson text"
    stages = []
    if pass_a:
        stages.append(f"**Decomposition.** The {source_word} was split into *groups* (one movement "
                      "exploration each) and numbered *steps* (one instruction or somatic event each). "
                      "Each step carries SPIFFER-Q tags: " +
                      "; ".join(f"{t} = {n} ({d})" for t, n, d in SPIFFER_Q) + ".")
    if somatic:
        stages.append("**Somatic analysis.** Each step was rewritten and read biomechanically: " +
                      _field_list(SOMATIC_FIELDS, field_descriptions))
    if language:
        stages.append("**Teacher's language.** Each step's wording was sorted into kinds of language, "
                      f"quoted literally from the {source_word}: " +
                      _field_list(LANGUAGE_FIELDS, field_descriptions) +
                      " A lesson-level summary characterizes the teacher's voice.")
    if condensed:
        stages.append("**Overview.** One card per group, written from the analyses above: " +
                      _field_list(CARD_FIELDS, field_descriptions) +
                      " A setting card describes the lesson as a whole.")
    if concepts:
        stages.append("**Concepts.** Search tags by category, a physical description of the lesson, "
                      "and search phrases a practitioner might use.")
    if positions:
        stages.append("**Positions.** The body configuration and action at each step, and the "
                      "distinct positions the lesson uses.")
    if stages:
        out += ["## How the analysis was made", ""]
        out += [f"{i}. {s}" for i, s in enumerate(stages, 1)]
        out += [""]
    else:
        out += ["## Analysis", "", f"This lesson has not been analyzed by {profile['model']}. "
                "Only the transcript is available.", ""]

    # ── Overview ───────────────────────────────────────────────────────────
    segments = (condensed or {}).get("segments", [])
    if segments:
        out += ["## Overview", ""]
        for seg in segments:
            setting = seg.get("setting") or {}
            if setting.get("title"):
                out += [f"### {setting['title']}", ""]
            for f in SETTING_FIELDS:
                if not _blank(setting.get(f)):
                    out += [f"**{_label(f).capitalize()}.** {_text(setting[f])}", ""]
            for card in seg.get("group_cards", []):
                out += [f"### Group {card.get('group_number', '?')}: {card.get('title') or card.get('group_label', '')}", ""]
                for f in CARD_FIELDS:
                    if not _blank(card.get(f)):
                        out += [f"**{_label(f).capitalize()}.** {_text(card[f])}", ""]

    # ── Groups and steps ───────────────────────────────────────────────────
    if pass_a:
        times = _step_times(pass_a, whisper_segments)
        somatic_by_step = {s.get("step_number"): s for s in (somatic or {}).get("enriched_steps", [])}
        language_by_step = {s.get("step_number"): s for s in (language or {}).get("enriched_steps", [])}
        position_by_step = {s.get("step_number"): s for s in (positions or {}).get("steps", [])}
        # Some decompositions tag each step with its group; others list step
        # numbers under each group. Accept both.
        steps = {s.get("step_number"): s for s in pass_a.get("steps", [])}
        steps_by_group: dict = {}
        for group in pass_a.get("groups") or []:
            for n in group.get("steps") or []:
                if n in steps:
                    steps_by_group.setdefault(group.get("group_number"), []).append(steps[n])
        if not steps_by_group:
            for step in pass_a.get("steps", []):
                steps_by_group.setdefault(step.get("group_number"), []).append(step)

        out += ["## Groups and steps", ""]
        if pass_a.get("home_position"):
            out += [f"Home position: {pass_a['home_position']}", ""]
        groups = pass_a.get("groups") or [{"group_number": g} for g in steps_by_group]
        for group in groups:
            number = group.get("group_number")
            heading = f"### Group {number}: {group.get('label', '')}".rstrip(": ")
            out += [heading, ""]
            if group.get("starting_position"):
                out += [f"Starting position: {group['starting_position']}", ""]
            if group.get("description"):
                out += [group["description"], ""]
            for step in steps_by_group.get(number, []):
                n = step.get("step_number")
                when = f" [{_clock(times[n][0])}–{_clock(times[n][1])}]" if n in times else ""
                tags = f" ({', '.join(step['tags'])})" if step.get("tags") else ""
                out += [f"**Step {n}**{when}{tags}: {step.get('instruction', '')}"]
                details = []
                pos = position_by_step.get(n)
                if pos and not _blank(pos.get("position")):
                    details.append(f"position: {pos['position']}")
                som = somatic_by_step.get(n) or {}
                details += [f"{_label(f)}: {_text(som[f])}" for f in SOMATIC_FIELDS if not _blank(som.get(f))]
                lang = language_by_step.get(n) or {}
                details += [f"{_label(f)}: {_text(lang[f])}" for f in LANGUAGE_FIELDS if not _blank(lang.get(f))]
                out += [f"- {d}" for d in details]
                out += [""]

    # ── Teacher's language ─────────────────────────────────────────────────
    summary = (language or {}).get("summary") or {}
    if summary:
        out += ["## Teacher's language", ""]
        if not _blank(summary.get("teaching_voice_summary")):
            out += [summary["teaching_voice_summary"], ""]
        for f in ("dominant_attention_mode", "effort_philosophy"):
            if not _blank(summary.get(f)):
                out += [f"**{_label(f).capitalize()}.** {_text(summary[f])}", ""]
        for f in ("speaker_principles", "signature_vocabulary"):
            items = summary.get(f) or []
            if items:
                out += [f"**{_label(f).capitalize()}:**"]
                for item in items:
                    out += [f"- {item if isinstance(item, str) else '; '.join(f'{k}: {v}' for k, v in item.items())}"]
                out += [""]

    # ── Concepts ───────────────────────────────────────────────────────────
    if concepts:
        out += ["## Concepts", ""]
        if concepts.get("distinctive_summary"):
            out += [concepts["distinctive_summary"], ""]
        by_category: dict = {}
        for c in concepts.get("concepts", []):
            by_category.setdefault(c.get("category", "other"), []).append(c.get("value", ""))
        for category, values in by_category.items():
            out += [f"- {_label(category)}: {', '.join(values)}"]
        if concepts.get("search_terms"):
            out += [f"- search phrases: {'; '.join(concepts['search_terms'])}"]
        out += [""]

    # ── Positions ──────────────────────────────────────────────────────────
    summary_positions = (positions or {}).get("position_summary") or []
    if summary_positions:
        out += ["## Positions used", ""]
        for p in summary_positions:
            if isinstance(p, dict):
                alternates = f" (also: {'; '.join(p['alternates'])})" if p.get("alternates") else ""
                out += [f"- {p.get('position', '')}{alternates}"]
            else:
                out += [f"- {p}"]
        out += [""]

    if saving:
        out += _saving_section(saving)

    out += ["## Transcript" if whisper_segments else "## Lesson text", "",
            _transcript(transcript, whisper_segments), ""]
    return "\n".join(out)


def _saving_section(saving: dict) -> list[str]:
    """How to send this conversation back to the lesson's Q&A history."""
    endpoint, token = saving["endpoint"], saving["token"]
    example = json.dumps({
        "token": token,
        "assistant": "<your model name>",
        "turns": conversation_import.EXAMPLE_TURNS,
    }, indent=2, ensure_ascii=False)
    out = [
        "## Saving this conversation",
        "",
        "The lesson-analysis tool keeps a history of questions asked about each lesson, and this "
        "conversation can be added to it. When the user asks you to save or export the "
        "conversation, reply with one JSON code block in exactly this form, and nothing else:",
        "",
        "```json", example, "```",
        "",
        "Include every exchange that came after this document, in order and word for word; do "
        "not summarize or shorten them. Leave this document itself out. Copy the token exactly.",
        "",
        "The user sends that JSON back in one of two ways.",
        "",
        f"**REST.** `POST {endpoint}` with the JSON as the body:",
        "",
        "```sh",
        f"curl -X POST {endpoint} \\",
        '  -H "Content-Type: application/json" --data-binary @conversation.json',
        "```",
        "",
        "A plain-text copy of the whole chat works too, with turns marked by speaker labels such "
        "as `User:` / `Assistant:` or `You said:` / `ChatGPT said:`. The token must appear "
        "somewhere in it (a copy that still includes this document does), or go in an "
        f"`Authorization: Bearer {token[:12]}…` header. The reply gives the number of exchanges "
        "saved and a link to the lesson, where they appear under History.",
        "",
    ]
    if saving.get("bucket"):
        out += [
            f"**S3.** Drop the same file in the inbox, with credentials for the site's AWS account:",
            "",
            "```sh",
            f"aws s3 cp conversation.json s3://{saving['bucket']}/conversations/inbox/",
            "```",
            "",
            "It is imported within seconds and moved to `conversations/imported/`, or to "
            "`conversations/rejected/` beside a `.error.txt` note saying why.",
            "",
        ]
    out += [
        "Saving a longer copy of the same conversation replaces the shorter one. The token only "
        "adds conversations to this lesson, under the account that exported it, and expires "
        f"{saving['expires']}.",
        "",
    ]
    return out


def _field_list(fields: list[str], descriptions: dict[str, str]) -> str:
    parts = []
    for f in fields:
        desc = descriptions.get(f, "")
        # Keep the definition's first sentence; the rest is writing guidance for the model.
        desc = re.split(r"(?<=[.!?])\s", desc.strip(), maxsplit=1)[0]
        parts.append(f"*{_label(f)}* — {desc.rstrip('.')}" if desc else f"*{_label(f)}*")
    return "; ".join(parts) + "."
