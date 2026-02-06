"""
Specialized critic nodes for the book pipeline.

Two independent critics that run in parallel on the assembled book:
- StudentCritic: reads as a first-time student, scoring engagement/accessibility
- SpecificityCritic: measures grounding in source material

Each critic processes chapters independently and returns structured JSON
via complete_structured_json().
"""

import json
import re
from typing import Optional

from bs4 import BeautifulSoup

try:
    from llm import LLM
except ImportError:
    from .llm import LLM


def _strip_prompt_elements(soup: BeautifulSoup) -> BeautifulSoup:
    """Remove prompt dialogs and prompt-link spans so critics only see reader-facing text."""
    for el in soup.find_all("dialog", class_="prompt-dialog"):
        el.decompose()
    for el in soup.find_all("span", class_="prompt-link"):
        el.decompose()
    return soup


def _html_to_text(html: str) -> str:
    """Convert HTML to plain text, collapsing whitespace."""
    soup = BeautifulSoup(html, "html.parser")
    _strip_prompt_elements(soup)
    return soup.get_text(separator="\n", strip=True)


def _strip_style(html: str) -> str:
    """Remove <style> blocks to save tokens."""
    return re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL)


def _extract_sections(chapter_html: str) -> list[dict]:
    """Parse a booklet chapter into named sections with their text content."""
    soup = BeautifulSoup(chapter_html, "html.parser")
    _strip_prompt_elements(soup)
    sections = []

    # The booklet cover
    cover = soup.find("div", class_="booklet-cover")
    if cover:
        sections.append({
            "name": "Cover",
            "text": cover.get_text(separator="\n", strip=True),
        })

    # Each booklet-section
    for sec in soup.find_all("section", class_="booklet-section"):
        heading = sec.find(["h2", "h3"])
        if heading:
            name = heading.get_text(strip=True)
        else:
            name = sec.get_text(strip=True)[:60]

        sections.append({
            "name": name,
            "text": sec.get_text(separator="\n", strip=True),
        })

    return sections


# -- JSON schemas for structured output --

STUDENT_CHAPTER_SCHEMA = {
    "type": "object",
    "properties": {
        "chapters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "lens": {"type": "string"},
                    "sections": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "section_name": {"type": "string"},
                                "recognition": {"type": "integer"},
                                "insight": {"type": "integer"},
                                "invitation": {"type": "integer"},
                                "accessibility": {"type": "integer"},
                                "engagement": {"type": "integer"},
                                "callouts": {"type": "string"},
                            },
                            "required": ["section_name", "recognition", "insight", "invitation", "accessibility", "engagement", "callouts"],
                        },
                    },
                    "overall_engagement": {"type": "integer"},
                },
                "required": ["title", "lens", "sections", "overall_engagement"],
            },
        },
    },
    "required": ["chapters"],
}

SPECIFICITY_CHAPTER_SCHEMA = {
    "type": "object",
    "properties": {
        "chapters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "lens": {"type": "string"},
                    "sections": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "section_name": {"type": "string"},
                                "concrete_references": {"type": "integer"},
                                "generic_sentences": {"type": "string"},
                                "specificity_score": {"type": "integer"},
                            },
                            "required": ["section_name", "concrete_references", "generic_sentences", "specificity_score"],
                        },
                    },
                    "overall_specificity": {"type": "integer"},
                },
                "required": ["title", "lens", "sections", "overall_specificity"],
            },
        },
    },
    "required": ["chapters"],
}


class StudentCritic:
    """Reads the book as a first-time student and scores engagement/accessibility."""

    def __init__(self, llm: LLM):
        self.llm = llm

    def review(self, chapters: list[dict], intro_text: str) -> dict:
        """Run student persona review across all chapters.

        Args:
            chapters: list of {"title", "lens", "html"} dicts
            intro_text: volume introduction text for context

        Returns:
            Structured dict matching STUDENT_CHAPTER_SCHEMA
        """
        chapters_text = ""
        for ch in chapters:
            clean = _strip_style(ch["html"])
            sections = _extract_sections(clean)
            sections_list = "\n".join(
                f"  - {s['name']}: {s['text'][:500]}..." if len(s['text']) > 500 else f"  - {s['name']}: {s['text']}"
                for s in sections
            )
            chapters_text += f"\n### {ch['title']} (Lens: {ch['lens']})\nSections:\n{sections_list}\n"

        prompt = f"""You are a first-time student who just finished a Feldenkrais Awareness Through
Movement lesson and is reading the companion booklet for the first time. You have NO prior
Feldenkrais experience and NO somatic education background.

The volume introduction describes the purpose:

---
{intro_text}
---

Here are the chapters to review:
{chapters_text}

For EACH section of EACH chapter, score on a 1-5 scale:

- **recognition** (1-5): Do I recognize my own experience in this text? Does it describe
  something I actually felt during the lesson? 1 = completely abstract, 5 = "yes, that's
  exactly what happened"
- **insight** (1-5): Does this section give me a new way to understand what happened?
  1 = tells me nothing new, 5 = genuinely shifts how I think about the experience
- **invitation** (1-5): Does the text invite me to explore further, or does it just
  assert conclusions? 1 = lectures at me, 5 = makes me want to try the lesson again
- **accessibility** (1-5): Can I understand this without specialized vocabulary?
  1 = jargon-heavy and opaque, 5 = clear and conversational
- **engagement** (1-5): Would I keep reading or put the booklet down?
  1 = tedious/boring, 5 = genuinely interesting

For sections scoring 3 or below on any dimension, provide specific callouts explaining
what went wrong. Quote specific text that lost you. Put all callouts in a single string,
separated by newlines.

Also provide an overall_engagement score (1-5) for each chapter as a whole.

Skip the Cover section — only review content sections."""

        return self.llm.complete_structured_json(prompt, STUDENT_CHAPTER_SCHEMA)


class SpecificityCritic:
    """Measures how well the booklet is grounded in the specific lesson's movements."""

    def __init__(self, llm: LLM):
        self.llm = llm

    def review(self, chapters: list[dict], intro_text: str) -> dict:
        """Run specificity review across all chapters.

        Args:
            chapters: list of {"title", "lens", "html"} dicts
            intro_text: volume introduction text for context

        Returns:
            Structured dict matching SPECIFICITY_CHAPTER_SCHEMA
        """
        chapters_text = ""
        for ch in chapters:
            clean = _strip_style(ch["html"])
            sections = _extract_sections(clean)
            sections_list = "\n".join(
                f"  - {s['name']}: {s['text'][:800]}..." if len(s['text']) > 800 else f"  - {s['name']}: {s['text']}"
                for s in sections
            )
            chapters_text += f"\n### {ch['title']} (Lens: {ch['lens']})\nSections:\n{sections_list}\n"

        prompt = f"""You are an editor checking whether a companion booklet for a Feldenkrais lesson
is grounded in the SPECIFIC movements and experiences of that lesson, or whether it contains
generic text that could appear in any lesson's booklet.

The volume introduction for context:

---
{intro_text}
---

Here are the chapters to review:
{chapters_text}

For EACH section of EACH chapter:

1. **concrete_references** (integer): Count the number of references to SPECIFIC movements,
   body parts in specific positions, or specific sensations that could ONLY apply to this
   particular lesson. Examples of concrete references:
   - "When you rolled your head to the right while pressing your left foot..."
   - "The moment you lifted your pelvis with bent knees..."
   - "Notice how the weight shifted to the outer edge of your left foot"

   Do NOT count vague references like "the movements" or "your body" or "this exploration."

2. **generic_sentences** (string): List sentences that could appear in ANY
   Feldenkrais lesson booklet with no modification, separated by newlines. These are the sentences that have
   no connection to the specific lesson content. Examples:
   - "The body gradually integrates these new patterns."
   - "Movement becomes more fluid and effortless."
   - "Your nervous system is always learning."
   - "Each repetition offers new possibilities."

   Only flag sentences that are truly interchangeable — if a sentence references a specific
   body part, direction, or position from the lesson, it's NOT generic even if the language
   is simple.

3. **specificity_score** (1-5): Overall grounding score for this section.
   1 = entirely generic/boilerplate, 5 = deeply rooted in this lesson's specific content.

Also provide an overall_specificity score (1-5) for each chapter as a whole.

Skip the Cover section — only review content sections."""

        return self.llm.complete_structured_json(prompt, SPECIFICITY_CHAPTER_SCHEMA)
