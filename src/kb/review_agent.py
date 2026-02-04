"""
Book review agent: reads the /book page as a reader would and provides
structured editorial feedback at gross (book-level) and fine (section-level)
granularity.

The volume introduction is treated as the review criteria — the agent reads it
first, then uses it to evaluate each chapter.

Supports multiple LLM providers for comparison. Each run produces a separate
markdown report tagged with the provider name.

Usage:
    # Review full book (local server must be running)
    uv run python -m src.kb.review_agent --url http://localhost:8000/book --provider gemini

    # Multiple providers — writes review_gemini.md, review_claude.md, etc.
    uv run python -m src.kb.review_agent --url http://localhost:8000/book \
        --output data/reviews/ --provider gemini --provider claude

    # Single chapter
    uv run python -m src.kb.review_agent \
        --chapter "http://localhost:8000/booklet?category=db:4&lesson=abfddaf1...&framework=VL-1&fill=true"

    # Gross review only (faster)
    uv run python -m src.kb.review_agent --url http://localhost:8000/book --gross-only

    # From saved HTML files
    uv run python -m src.kb.review_agent --input booklet.html --output review.md
"""

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse

import requests
from bs4 import BeautifulSoup

try:
    from llm import LLM
except ImportError:
    from .llm import LLM

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Section:
    """A named section within a booklet chapter."""
    name: str       # "Cover", "Journey Part 3", "The Riser", etc.
    html: str       # the section HTML
    text: str       # stripped text for summaries


@dataclass
class FixSuggestion:
    """A machine-readable fix suggestion."""
    excerpt: str        # the problematic text (quoted)
    issue: str          # what's wrong
    suggestion: str     # rewritten text or approach
    fix_target: str     # e.g. "prompt:pedagogical-gloss", "render:booklet_journey"


@dataclass
class SectionFeedback:
    """Feedback for one section of a chapter."""
    section_name: str
    rating: str         # "adequate", "needs_work", "broken"
    feedback: str       # what's wrong
    fixes: list[FixSuggestion] = field(default_factory=list)


@dataclass
class ChapterReview:
    """Fine-level review for one chapter/booklet."""
    title: str
    lens: str           # "VL-1", etc.
    sections: list[SectionFeedback] = field(default_factory=list)


@dataclass
class GrossReview:
    """Book-level review — problems only."""
    coherence: str          # gaps in how the book hangs together
    voice_issues: str       # tone inconsistencies
    intro_gaps: str         # what the intro doesn't set up
    repetition: str         # what's repeated across chapters
    lens_bleed: str         # where chapters overlap on lens territory
    weakest_chapter: str    # which chapter is weakest and why
    chapter_notes: list[dict] = field(default_factory=list)  # {title, lens, problems}


@dataclass
class BookReview:
    """Complete review from one provider."""
    provider: str       # "gemini", "claude", "openai"
    gross: Optional[GrossReview] = None
    chapters: list[ChapterReview] = field(default_factory=list)


# ---------------------------------------------------------------------------
# ReviewAgent
# ---------------------------------------------------------------------------

@dataclass
class ReviewAgent:
    """Reviews the book or individual chapters via LLM.

    Phases:
        1. Fetch & parse the book page and chapter pages
        2. Gross review (book-level) using volume intro + chapter summaries
        3. Fine review (per chapter) with section-by-section feedback
        4. Assemble markdown report
    """
    llm: LLM
    base_url: str = "http://localhost:8000"
    verbose: bool = False

    # --- Fetch helpers ---

    def _fetch(self, url: str) -> str:
        """HTTP GET, return response text."""
        if self.verbose:
            logger.info(f"Fetching: {url}")
        resp = requests.get(url, timeout=120)
        resp.raise_for_status()
        return resp.text

    def _strip_style(self, html: str) -> str:
        """Remove <style> blocks to save tokens. Keep structural HTML."""
        return re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL)

    def _strip_nav(self, html: str) -> str:
        """Remove navigation/back-link elements."""
        return re.sub(r'<a[^>]*class="back-link"[^>]*>.*?</a>', '', html, flags=re.DOTALL)

    def _html_to_text(self, html: str) -> str:
        """Convert HTML to plain text, collapsing whitespace."""
        soup = BeautifulSoup(html, "html.parser")
        return soup.get_text(separator="\n", strip=True)

    # --- Parse helpers ---

    def _extract_volume_intro(self, book_html: str) -> str:
        """Extract the filled volume introduction from the /book page."""
        soup = BeautifulSoup(book_html, "html.parser")
        # The volume intro is in a div with class "query-result" or "filled"
        # after the llm-query block. Look for the intro content.
        intro_div = soup.find("div", class_="query-result")
        if intro_div:
            return intro_div.get_text(separator="\n", strip=True)

        # Fallback: look for filled content directly
        filled = soup.find("div", class_="llm-filled")
        if filled:
            return filled.get_text(separator="\n", strip=True)

        # Last resort: grab everything between the first <h2> and the booklet list
        # The intro text appears between the header and "The Booklets" heading
        text_parts = []
        for elem in soup.find_all(["p", "div"]):
            text = elem.get_text(strip=True)
            if text and "The Booklets" not in text and len(text) > 50:
                text_parts.append(text)
                if len(text_parts) >= 5:
                    break

        return "\n\n".join(text_parts) if text_parts else ""

    def _extract_booklet_links(self, book_html: str) -> list[dict]:
        """Parse booklet links from the /book page (main booklets only, not alternates).

        Returns list of {url, title, lens, lens_subtitle}.
        """
        soup = BeautifulSoup(book_html, "html.parser")
        links = []

        # Find the "The Booklets" heading and get its next <ol> sibling
        # This avoids picking up alternates which are under a separate heading
        booklets_heading = None
        for h2 in soup.find_all("h2"):
            if "Booklet" in h2.get_text():
                booklets_heading = h2
                break

        if booklets_heading:
            ol = booklets_heading.find_next_sibling("ol")
            search_scope = ol.find_all("li") if ol else []
        else:
            # Fallback: use all list items
            search_scope = soup.find_all("li")

        for li in search_scope:
            a = li.find("a")
            if not a or "/booklet" not in (a.get("href") or ""):
                continue

            lens_span = li.find("span", class_="lens")
            lens_text = lens_span.get_text(strip=True) if lens_span else ""

            # Parse lens ID from the URL's framework param
            href = a["href"]
            parsed = urlparse(href)
            params = parse_qs(parsed.query)
            framework = params.get("framework", [""])[0]

            links.append({
                "url": href,
                "title": a.get_text(strip=True),
                "lens": framework,
                "lens_description": lens_text,
            })

        return links

    def _ensure_fill(self, url: str) -> str:
        """Add fill=true to a URL if not already present."""
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        if "fill" not in params:
            params["fill"] = ["true"]
            new_query = urlencode(params, doseq=True)
            return urlunparse(parsed._replace(query=new_query))
        return url

    def _make_absolute(self, url: str) -> str:
        """Make a relative URL absolute using base_url."""
        if url.startswith("http"):
            return url
        return urljoin(self.base_url, url)

    def _extract_chapter_sections(self, chapter_html: str) -> list[Section]:
        """Parse a booklet into named sections."""
        soup = BeautifulSoup(chapter_html, "html.parser")
        sections = []

        # The booklet cover
        cover = soup.find("div", class_="booklet-cover")
        if cover:
            sections.append(Section(
                name="Cover",
                html=str(cover),
                text=cover.get_text(separator="\n", strip=True),
            ))

        # Each booklet-section
        for sec in soup.find_all("section", class_="booklet-section"):
            # Determine section name from its heading
            heading = sec.find(["h2", "h3"])
            if heading:
                name = heading.get_text(strip=True)
            else:
                # Use first significant text
                name = sec.get_text(strip=True)[:60]

            sections.append(Section(
                name=name,
                html=str(sec),
                text=sec.get_text(separator="\n", strip=True),
            ))

        return sections

    # --- LLM calls ---

    def _gross_review(self, intro_text: str, chapter_summaries: list[dict]) -> GrossReview:
        """Run gross (book-level) review via LLM."""
        summaries_text = ""
        for i, ch in enumerate(chapter_summaries, 1):
            summaries_text += f"\n### Chapter {i}: {ch['title']} ({ch['lens']})\n"
            summaries_text += ch["summary"] + "\n"

        prompt = f"""You are a critical editor reviewing a volume of companion booklets for
Feldenkrais ATM lessons. Your job is to find problems, not give praise.
Skip anything that works — only report what doesn't.

The volume's own introduction describes its purpose and audience:

---
{intro_text}
---

The volume contains {len(chapter_summaries)} chapters. Here is a summary of each:
{summaries_text}

NOTE: The Engine (VL-4) booklet is intentionally excluded from this review scope.
The intro references five lenses, and five booklets exist in the final volume — but
The Engine chapter is being reworked separately. Do NOT flag its absence as an issue.
Focus only on the chapters provided above.

Report ONLY problems in these categories:

1. **Coherence gaps**: Where does the book not hang together? Where does the flow
   from one chapter to the next feel broken or arbitrary?
2. **Voice inconsistencies**: Where does the tone shift or feel off? Where does the
   writing feel like it was generated by different authors?
3. **Intro gaps**: What does the volume introduction fail to set up? What expectations
   does it create that the chapters don't deliver on?
4. **Repetition**: What concepts, phrases, or framings are repeated too much across
   chapters? Be specific — quote the repeated elements.
5. **Lens bleed**: The five lenses are not conceptually orthogonal — some thematic
   overlap is expected and healthy. Only flag lens bleed when two chapters feel
   INTERCHANGEABLE — covering the same movements with the same conclusions under
   different names. Minor shared themes are fine.
6. **Weakest chapter**: Which chapter is the weakest and why?
7. **Per-chapter notes**: For each chapter, list 1-3 specific problems. Skip chapters
   that work fine.

If something works, don't mention it. No praise. Every sentence should identify
a problem or suggest a change.

Format your response as JSON:
{{
    "coherence": "...",
    "voice_issues": "...",
    "intro_gaps": "...",
    "repetition": "...",
    "lens_bleed": "...",
    "weakest_chapter": "...",
    "chapter_notes": [
        {{"title": "...", "lens": "...", "problems": "..."}}
    ]
}}"""

        raw = self.llm.complete(prompt, max_tokens=8192)

        # Parse JSON from response (handle markdown code fences)
        data = self._parse_json_response(raw)

        return GrossReview(
            coherence=data.get("coherence", ""),
            voice_issues=data.get("voice_issues", ""),
            intro_gaps=data.get("intro_gaps", ""),
            repetition=data.get("repetition", ""),
            lens_bleed=data.get("lens_bleed", ""),
            weakest_chapter=data.get("weakest_chapter", ""),
            chapter_notes=data.get("chapter_notes", []),
        )

    def _fine_review(self, intro_text: str, chapter_html: str,
                     title: str, lens: str) -> ChapterReview:
        """Run fine (section-level) review for one chapter."""
        # Strip style to save tokens
        clean_html = self._strip_style(chapter_html)
        clean_html = self._strip_nav(clean_html)

        prompt = f"""You are a critical editor reviewing one chapter of a companion booklet volume.
Your job is to find problems and suggest specific fixes. Skip anything that works.

The volume's introduction describes the audience and purpose:

---
{intro_text}
---

This chapter is "{title}" viewed through the {lens} lens.

Here is the chapter HTML:

---
{clean_html}
---

IMPORTANT: The booklet sections are NOT conceptually orthogonal. Some thematic
overlap and bleed between sections is expected and desirable — the same lesson
is being explored from different angles. Do NOT flag overlap as a problem unless
it is pure redundancy (the same sentence or idea repeated verbatim with no new
angle). A concept appearing in both the Person Inside narrative and the Journey
dialogue is fine if each adds something.

For each section of the chapter, report ONLY problems:

- **Person Inside Narrative**: Does the first-person story feel generic or
  disconnected from the actual movements in this lesson? Does it read like a
  template that could apply to any lesson? Is it too short or too long?
- **Journey (each part)**: Are pedagogical glosses generic rather than specific to
  the actual movements described? Do the Person Inside / Shadow dialogue voices
  sound fake, repetitive, or disconnected from what the teacher is actually asking?
- **How Feldenkrais Lessons Work**: Does this section contextualize the key concepts
  for THIS lesson, or is it generic boilerplate?
- **How the Lesson Washed Away Effort**: Does the washing gradient feel earned by
  the movements described, or does it just assert transformation?

For each problem found:
1. Quote the problematic text (the exact excerpt)
2. Explain what's wrong
3. Suggest a fix (rewritten text or a different approach)
4. Identify the fix target — where in the system to make the change:
   - "prompt:pedagogical-gloss" — the LLM prompt that generated pedagogical glosses
   - "prompt:person-inside" — the Person Inside voice prompt
   - "prompt:shadow-voice" — the Shadow voice prompt
   - "prompt:lens-synthesis" — the Person Inside narrative prompt
   - "prompt:lesson-concepts" — the How Feldenkrais Lessons Work prompt
   - "prompt:volume-intro" — the volume intro prompt
   - "render:booklet_journey" — the render_booklet_journey() function in web.py
   - "render:booklet_person_inside_narrative" — the Person Inside narrative renderer
   - "render:booklet_washing" — the washing gradient section renderer
   - "render:booklet_concepts" — the lesson concepts section renderer
   - "data:somatic_narrative" — the Pass B somatic narrative data
   - "data:pass_a" — the Pass A analysis data
   - "data:vl_analysis" — the VL lens analysis data

Rate each section: "adequate" (minor issues only), "needs_work" (significant issues),
or "broken" (doesn't serve the reader). Do NOT use "strong", "good", or any positive
rating — if a section works, skip it entirely.

Format your response as JSON:
{{
    "sections": [
        {{
            "section_name": "...",
            "rating": "adequate|needs_work|broken",
            "feedback": "...",
            "fixes": [
                {{
                    "excerpt": "quoted text from the chapter",
                    "issue": "what's wrong",
                    "suggestion": "rewritten text or approach",
                    "fix_target": "prompt:pedagogical-gloss"
                }}
            ]
        }}
    ]
}}

Only include sections that have problems. Skip sections that work."""

        raw = self.llm.complete(prompt, max_tokens=16384)
        data = self._parse_json_response(raw)

        chapter = ChapterReview(title=title, lens=lens)
        for sec_data in data.get("sections", []):
            fixes = []
            for fix_data in sec_data.get("fixes", []):
                fixes.append(FixSuggestion(
                    excerpt=fix_data.get("excerpt", ""),
                    issue=fix_data.get("issue", ""),
                    suggestion=fix_data.get("suggestion", ""),
                    fix_target=fix_data.get("fix_target", ""),
                ))
            chapter.sections.append(SectionFeedback(
                section_name=sec_data.get("section_name", ""),
                rating=sec_data.get("rating", "needs_work"),
                feedback=sec_data.get("feedback", ""),
                fixes=fixes,
            ))

        return chapter

    def _parse_json_response(self, raw: str) -> dict:
        """Parse JSON from LLM response, handling markdown code fences."""
        # Strip markdown code fences if present
        text = raw.strip()
        if text.startswith("```"):
            # Remove opening fence (with optional language tag)
            text = re.sub(r'^```\w*\n?', '', text)
            # Remove closing fence
            text = re.sub(r'\n?```$', '', text)
            text = text.strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Try to find JSON object in the text
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass
            logger.error(f"Failed to parse JSON from LLM response:\n{raw[:500]}")
            return {}

    # --- Main entry points ---

    def review_book(self, url: Optional[str] = None, gross_only: bool = False) -> BookReview:
        """Review the full book. Returns structured feedback.

        Args:
            url: Book page URL. Defaults to {base_url}/book
            gross_only: If True, skip per-chapter fine review
        """
        url = url or f"{self.base_url}/book"
        provider_name = self.llm.provider
        if self.verbose:
            print(f"Reviewing book with {provider_name} ({self.llm.model})...")

        # Phase 1: Fetch the book page (with fill=true for the intro)
        book_url = self._ensure_fill(url)
        if self.verbose:
            print(f"  Fetching book page: {book_url}")
        book_html = self._fetch(book_url)

        # Extract volume intro and booklet links
        intro_text = self._extract_volume_intro(book_html)
        if not intro_text:
            logger.warning("Could not extract volume intro text")
            intro_text = "(Volume introduction not available)"

        booklet_links = self._extract_booklet_links(book_html)
        if self.verbose:
            print(f"  Found {len(booklet_links)} booklet links")
            print(f"  Intro text: {len(intro_text)} chars")

        # Phase 1b: Fetch each chapter
        chapters_data = []
        for link in booklet_links:
            chapter_url = self._make_absolute(self._ensure_fill(link["url"]))
            if self.verbose:
                print(f"  Fetching chapter: {link['title']} ({link['lens']})")
            chapter_html = self._fetch(chapter_url)
            chapter_text = self._html_to_text(self._strip_style(chapter_html))

            # Summarize for gross review (first ~2000 chars of text)
            summary = chapter_text[:2000]
            if len(chapter_text) > 2000:
                summary += "\n[... truncated for summary ...]"

            chapters_data.append({
                "title": link["title"],
                "lens": link["lens"],
                "lens_description": link["lens_description"],
                "html": chapter_html,
                "text": chapter_text,
                "summary": summary,
            })

        # Phase 2: Gross review
        if self.verbose:
            print("  Running gross (book-level) review...")
        gross = self._gross_review(intro_text, chapters_data)

        review = BookReview(provider=provider_name, gross=gross)

        # Phase 3: Fine review (per chapter)
        if not gross_only:
            for ch in chapters_data:
                if self.verbose:
                    print(f"  Running fine review: {ch['title']} ({ch['lens']})...")
                chapter_review = self._fine_review(
                    intro_text, ch["html"], ch["title"], ch["lens"]
                )
                review.chapters.append(chapter_review)

        return review

    def review_chapter(self, url: Optional[str] = None, html: Optional[str] = None,
                       title: str = "", lens: str = "") -> ChapterReview:
        """Review a single chapter/booklet.

        Provide either url or html. If url, it will be fetched (with fill=true).
        """
        if url:
            chapter_url = self._ensure_fill(self._make_absolute(url))
            html = self._fetch(chapter_url)

        if not html:
            raise ValueError("Provide either url or html")

        # Try to extract title/lens from HTML if not provided
        if not title or not lens:
            soup = BeautifulSoup(html, "html.parser")
            if not title:
                cover_title = soup.find(class_="cover-title")
                if cover_title:
                    title = cover_title.get_text(strip=True)
                else:
                    title_tag = soup.find("title")
                    title = title_tag.get_text(strip=True) if title_tag else "Unknown"
            if not lens:
                cover_lens = soup.find(class_="cover-lens")
                if cover_lens:
                    text = cover_lens.get_text(strip=True)
                    match = re.search(r'VL-\d', text)
                    if match:
                        lens = match.group()

        # For single-chapter review, we need a volume intro.
        # Try to fetch it from the book page.
        intro_text = ""
        try:
            book_html = self._fetch(self._ensure_fill(f"{self.base_url}/book"))
            intro_text = self._extract_volume_intro(book_html)
        except Exception:
            logger.warning("Could not fetch volume intro for context")

        if not intro_text:
            intro_text = "(Volume introduction not available — review without full context)"

        return self._fine_review(intro_text, html, title, lens)


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------

def format_report(review: BookReview) -> str:
    """Format a BookReview as markdown."""
    lines = []
    lines.append(f"# Book Review ({review.provider})\n")

    # Gross review
    if review.gross:
        g = review.gross
        lines.append("## Book-Level Problems\n")

        if g.coherence:
            lines.append("### Coherence")
            lines.append(g.coherence + "\n")

        if g.voice_issues and g.voice_issues.lower() != "none":
            lines.append("### Voice Inconsistencies")
            lines.append(g.voice_issues + "\n")

        if g.intro_gaps:
            lines.append("### Introduction Gaps")
            lines.append(g.intro_gaps + "\n")

        if g.repetition:
            lines.append("### Repetition")
            lines.append(g.repetition + "\n")

        if g.lens_bleed:
            lines.append("### Lens Bleed")
            lines.append(g.lens_bleed + "\n")

        if g.weakest_chapter:
            lines.append("### Weakest Chapter")
            lines.append(g.weakest_chapter + "\n")

        if g.chapter_notes:
            lines.append("### Per-Chapter Notes\n")
            for note in g.chapter_notes:
                lines.append(f"**{note.get('title', '?')}** ({note.get('lens', '?')})")
                lines.append(note.get("problems", "(no notes)") + "\n")

    # Fine reviews
    for ch in review.chapters:
        lines.append(f"## Chapter: {ch.title} ({ch.lens})\n")

        if not ch.sections:
            lines.append("*No issues found.*\n")
            continue

        for sec in ch.sections:
            lines.append(f"### {sec.section_name} — {sec.rating}\n")
            if sec.feedback:
                lines.append(sec.feedback + "\n")

            for fix in sec.fixes:
                if fix.excerpt:
                    lines.append(f"> {fix.excerpt}\n")
                if fix.issue:
                    lines.append(f"**Problem:** {fix.issue}\n")
                if fix.suggestion:
                    lines.append(f"**Suggestion:** {fix.suggestion}\n")
                if fix.fix_target:
                    lines.append(f"**Fix target:** `{fix.fix_target}`\n")
                lines.append("")  # blank line between fixes

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse
    import os
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Review the companion booklet volume",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  # Full book review with gemini
  uv run python -m src.kb.review_agent --url http://localhost:8000/book --provider gemini

  # Multiple providers
  uv run python -m src.kb.review_agent --url http://localhost:8000/book \\
      --output data/reviews/ --provider gemini --provider claude --provider openai

  # Single chapter
  uv run python -m src.kb.review_agent \\
      --chapter "http://localhost:8000/booklet?category=db:4&lesson=abfddaf1...&framework=VL-1"

  # Gross review only
  uv run python -m src.kb.review_agent --url http://localhost:8000/book --gross-only

  # From saved HTML
  uv run python -m src.kb.review_agent --input saved_chapter.html --output review.md
""",
    )

    parser.add_argument("--url", default="http://localhost:8000/book",
                        help="Book page URL (default: http://localhost:8000/book)")
    parser.add_argument("--chapter", help="Review a single chapter URL instead of the full book")
    parser.add_argument("--input", help="Read chapter HTML from a file instead of fetching")
    parser.add_argument("--output", "-o", help="Output file or directory for report(s)")
    parser.add_argument("--provider", action="append", default=[],
                        help="LLM provider(s): gemini, openai, anthropic. Can repeat.")
    parser.add_argument("--gross-only", action="store_true",
                        help="Only run book-level review (skip per-chapter)")
    parser.add_argument("--verbose", "-v", action="store_true")

    args = parser.parse_args()

    # Load .env if available
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    # Import get_llm from web.py's config
    # We replicate the LLM config here to avoid importing the full web module
    LLM_CONFIGS = {
        "gemini": {
            "provider": "gemini",
            "model": "gemini-3-flash-preview",
            "api_key_env": "GOOGLE_API_KEY",
            "default_max_tokens": 65536,
        },
        "openai": {
            "provider": "openai",
            "model": "gpt-5",
            "api_key_env": "OPENAI_API_KEY",
            "default_max_tokens": 128000,
        },
        "anthropic": {
            "provider": "anthropic",
            "model": "claude-sonnet-4-20250514",
            "api_key_env": "ANTHROPIC_API_KEY",
            "default_max_tokens": 16384,
        },
    }

    providers = args.provider or ["gemini"]

    for provider_key in providers:
        config = LLM_CONFIGS.get(provider_key)
        if not config:
            print(f"Unknown provider: {provider_key}. Choose from: {list(LLM_CONFIGS.keys())}")
            sys.exit(1)

        api_key = os.environ.get(config["api_key_env"])
        if not api_key:
            print(f"{config['api_key_env']} not set — skipping {provider_key}")
            continue

        llm = LLM(
            provider=config["provider"],
            model=config["model"],
            api_key=api_key,
            default_max_tokens=config["default_max_tokens"],
        )

        agent = ReviewAgent(llm=llm, base_url=args.url.rsplit("/book", 1)[0] or "http://localhost:8000",
                            verbose=args.verbose)

        if args.input:
            # Review from saved HTML file
            html = Path(args.input).read_text()
            review = BookReview(provider=provider_key)
            ch_review = agent.review_chapter(html=html)
            review.chapters.append(ch_review)
        elif args.chapter:
            # Single chapter review
            review = BookReview(provider=provider_key)
            ch_review = agent.review_chapter(url=args.chapter)
            review.chapters.append(ch_review)
        else:
            # Full book review
            review = agent.review_book(url=args.url, gross_only=args.gross_only)

        report = format_report(review)

        # Write output
        if args.output:
            output_path = Path(args.output)
            if output_path.is_dir() or (len(providers) > 1 and not output_path.suffix):
                output_path.mkdir(parents=True, exist_ok=True)
                output_file = output_path / f"review_{provider_key}.md"
            else:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_file = output_path

            output_file.write_text(report)
            print(f"Wrote: {output_file}")
        else:
            print(report)


if __name__ == "__main__":
    main()
