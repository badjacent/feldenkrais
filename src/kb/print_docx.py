"""Annotated Step Transcript: the editable DOCX made from a print model.

docs/LESSON_PRINT_FORMATS_SPEC.md, Output B. The print model (print_prep)
supplies groups, steps, positions, summaries and quote offsets; the transcript
comes straight from the stored segments, never from the LLM. Each step's text is
its segments joined by single spaces, with whitespace runs inside a segment
collapsed to one space (the only normalization). Paragraph breaks are placed
only at spaces after sentence ends, and the document is refused if joining its
paragraphs does not give back that text exactly.
"""

import io
import re

from docx import Document
from docx.shared import Inches, Pt, RGBColor

SENTENCES_PER_PARAGRAPH = 3
# A space that follows sentence-ending punctuation (and any closing quote or bracket).
_SENTENCE_BREAK = re.compile(r"(?<=[.!?…])([\"”’)\]]*) ")


class IncompleteTranscriptError(ValueError):
    """The lesson's segments do not cover the transcript exactly once."""


def step_text(segment_ids: list[int], seg_text: dict[int, str]) -> str:
    """A step's transcript: its segments in order, whitespace-normalized."""
    return " ".join(" ".join(seg_text.get(sid, "").split()) for sid in segment_ids).strip()


def reflow(text: str, per_paragraph: int = SENTENCES_PER_PARAGRAPH) -> list[str]:
    """Split text into paragraphs of about ``per_paragraph`` sentences.

    Breaks fall only on the single space after a sentence end, so
    ``" ".join(reflow(text)) == text``.
    """
    if not text:
        return []
    sentences, start = [], 0
    for match in _SENTENCE_BREAK.finditer(text):
        sentences.append(text[start:match.end() - 1])  # drop the breaking space
        start = match.end()
    sentences.append(text[start:])
    paragraphs = [" ".join(sentences[i:i + per_paragraph]) for i in range(0, len(sentences), per_paragraph)]
    if " ".join(paragraphs) != text:
        raise AssertionError("reflow changed the transcript text")
    return paragraphs


def build_annotated_transcript(model: dict, pass_a: dict, title: str,
                               source_label: str | None = None) -> bytes:
    """Render the Annotated Step Transcript for one prepared lesson."""
    coverage = model.get("validation", {}).get("coverage_errors") or []
    if coverage:
        raise IncompleteTranscriptError("; ".join(coverage))
    seg_text = {s["id"]: s.get("text", "") for s in pass_a.get("pass_0", {}).get("segments", [])}
    steps = {s["step_number"]: s for s in model["steps"]}

    doc = Document()
    section = doc.sections[0]
    section.page_width, section.page_height = Inches(8.5), Inches(11)
    for side in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
        setattr(section, side, Inches(1))
    normal = doc.styles["Normal"]
    normal.font.name = "Georgia"
    normal.font.size = Pt(11)

    doc.add_heading(title, level=0)
    meta = "Annotated step transcript"
    if source_label:
        meta += f" · {source_label}"
    doc.add_paragraph(meta).runs[0].font.color.rgb = RGBColor(0x66, 0x66, 0x66)

    emitted = []
    for group in model["groups"]:
        heading = doc.add_heading(group.get("label") or f"Group {group['group_number']}", level=1)
        heading.paragraph_format.keep_with_next = True
        for number in group["step_numbers"]:
            step = steps[number]
            h = doc.add_heading(f"Step {number}", level=2)
            h.paragraph_format.keep_with_next = True
            if step.get("position"):
                pos = doc.add_paragraph()
                pos.paragraph_format.keep_with_next = True
                pos.add_run(step["position"]).bold = True
            summary = doc.add_paragraph()
            summary.paragraph_format.keep_with_next = True
            summary.add_run(step.get("brief_summary", "")).italic = True
            for quote in step.get("notable_quotes", []):
                excerpt = seg_text.get(quote["segment_id"], "")[quote["start_offset"]:quote["end_offset"]]
                # Same whitespace normalization as the transcript: words unchanged.
                doc.add_paragraph(f"“{' '.join(excerpt.split())}”", style="List Bullet")
            label = doc.add_paragraph()
            label.paragraph_format.space_before = Pt(6)
            label.paragraph_format.keep_with_next = True
            label.add_run("Transcript").bold = True
            text = step_text(step["segment_ids"], seg_text)
            paragraphs = reflow(text)
            for paragraph in paragraphs:
                p = doc.add_paragraph(paragraph)
                p.paragraph_format.space_after = Pt(10)
            emitted.append((text, paragraphs))

    # Every segment once, in order, with wording unchanged.
    expected = [step_text(steps[n]["segment_ids"], seg_text) for g in model["groups"] for n in g["step_numbers"]]
    if [" ".join(p) for _, p in emitted] != expected:
        raise AssertionError("document transcript does not match the stored segments")

    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()
