"""Generate a Kindle-ready EPUB from a cached lesson analysis.

Usage:
    python scripts/generate_epub.py <doc_id> [output.epub]

Example:
    python scripts/generate_epub.py abfddaf1-e837-4ed5-9d59-6e53056eb759 lesson.epub
"""

import hashlib
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root
load_dotenv(Path(__file__).parent.parent / ".env")

from ebooklib import epub

# Add src/kb to path for database module
sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "kb"))
from database import Database
from web import VL_BOOKLET_INTROS as VL_INTROS  # Single source of truth

SPIFFER = {
    "S": "A moment of sensing — turning attention inward to feel what's actually happening.",
    "P": "A pause to let things settle. Integration happens in the quiet moments.",
    "I": "A mental image or metaphor to guide the movement from the inside.",
    "F": "A functional goal — connecting this movement to something you actually do in life.",
    "E": "An invitation to reduce effort, to find the movement with less force.",
    "R": "Noticing how parts of your body are connected — how one movement ripples through.",
    "Q": "The teacher poses a question. Not something to answer, but something to feel into.",
}

# --- Shared CSS for all chapters ---
CHAPTER_CSS = """
body {
    font-family: Georgia, serif;
    line-height: 1.7;
    color: #222;
}
h1 { font-size: 1.6em; margin-bottom: 0.5em; }
h2 { font-size: 1.3em; margin-top: 1.5em; margin-bottom: 0.5em; }
h3 { font-size: 1.1em; margin-top: 1.2em; margin-bottom: 0.4em; }
blockquote {
    font-style: italic;
    margin: 1em 0;
    padding: 0.5em 1em;
    border-left: 3px solid #666;
}
.home-position {
    font-style: italic;
    padding: 0.5em 1em;
    border-left: 3px solid #888;
    margin: 1em 0;
}
.neg-badge {
    display: inline-block;
    font-size: 0.85em;
    font-weight: bold;
    padding: 2px 10px;
    border-radius: 10px;
    color: white;
    margin-bottom: 0.5em;
}
.voice-box {
    margin: 0.8em 0;
    padding: 0.6em 1em;
    border-left: 3px solid #666;
}
.voice-box.person-inside { border-left-color: #3498db; }
.voice-box.shadow-voice { border-left-color: #c0392b; }
.voice-label {
    font-size: 0.8em;
    font-weight: bold;
    text-transform: uppercase;
    margin-bottom: 0.3em;
}
.person-inside .voice-label { color: #3498db; }
.shadow-voice .voice-label { color: #c0392b; }
ol, ul { margin: 0.5em 0 0.5em 1.5em; }
li { margin-bottom: 0.4em; }
.spiffer-hint {
    font-size: 0.9em;
    color: #666;
    font-style: italic;
}
.state {
    margin: 0.4em 0;
    padding: 0.4em 0.8em;
}
.state-bracing { border-left: 3px solid #c0392b; }
.state-softening { border-left: 3px solid #e67e22; }
.state-transmission { border-left: 3px solid #27ae60; }
"""


def parse_seq_to_step_nums(seq_val: str) -> set[int]:
    """Parse '1-3' or '4, 6-7' into a set of step numbers."""
    import re
    nums = set()
    for part in re.split(r'[,;]\s*', str(seq_val)):
        part = part.strip()
        m = re.match(r'(\d+)\s*[-–]\s*(\d+)', part)
        if m:
            for n in range(int(m.group(1)), int(m.group(2)) + 1):
                nums.add(n)
        else:
            m2 = re.match(r'(\d+)', part)
            if m2:
                nums.add(int(m2.group(1)))
    return nums


def get_neg_state(row, headers):
    for col, h in headers.items():
        if h and ("negotiation" in h.lower() or "state" in h.lower()):
            return row.get(col, "")
    return ""


def get_col_by_keyword(row, headers, kw):
    for col, h in headers.items():
        if h and kw.lower() in h.lower():
            return row.get(col, "")
    return ""


def make_chapter(title, body_html):
    """Create an epub chapter."""
    ch = epub.EpubHtml(title=title, file_name=f"ch_{title.lower().replace(' ', '_')[:30]}.xhtml")
    ch.content = f"<html><head><style>{CHAPTER_CSS}</style></head><body>{body_html}</body></html>"
    return ch


def generate_epub(analysis: dict, lesson_title: str, output_path: str):
    """Generate an EPUB from a cached analysis result."""
    pass_a = analysis.get("pass_a_result", {})
    vl_analyses = analysis.get("vl_analyses", {})

    title = pass_a.get("lesson_title", "") or lesson_title
    framework_id = pass_a.get("primary_framework", "VL-1")
    intro = VL_INTROS.get(framework_id, VL_INTROS["VL-1"])

    # Get primary VL analysis
    primary_vl = vl_analyses.get(framework_id, {})
    if primary_vl.get("status") != "complete":
        for fid, vl in vl_analyses.items():
            if vl.get("status") == "complete":
                primary_vl = vl
                framework_id = fid
                intro = VL_INTROS.get(fid, VL_INTROS["VL-1"])
                break

    result = primary_vl.get("result", {})
    somatic = result.get("somatic_narrative", {})
    negotiation = result.get("negotiation_framework", {})
    table = result.get("analytical_table", {})
    col_headers = table.get("column_headers", {})
    table_rows = table.get("rows", [])

    groups = pass_a.get("groups", [])
    steps = pass_a.get("steps", [])
    home = pass_a.get("home_position", "")
    step_lookup = {s["step_number"]: s for s in steps if "step_number" in s}

    # Map groups to table rows
    group_rows = {}
    for g in groups:
        g_steps = set(g.get("steps", []))
        matched = []
        for row in table_rows:
            r_steps = parse_seq_to_step_nums(row.get("column1", ""))
            if r_steps & g_steps:
                matched.append(row)
        group_rows[g.get("group_number")] = matched

    # --- Build the book ---
    book = epub.EpubBook()
    book.set_identifier(f"feldenkrais-{framework_id}-{hash(title)}")
    book.set_title(title)
    book.set_language("en")
    book.add_author("AI on Your Side")

    chapters = []

    # -- Title page --
    ch = make_chapter("Title", f"""
        <h1 style="text-align: center; margin-top: 3em;">{title}</h1>
        <p style="text-align: center; font-style: italic; color: #666; margin-top: 1em;">
            A Companion to This Lesson
        </p>
        <p style="text-align: center; margin-top: 2em; color: #666;">
            Explored through the lens of<br/>
            <strong><em>{intro['name']}</em></strong><br/>
            <span style="font-size: 0.9em;">{intro['subtitle']}</span>
        </p>
    """)
    chapters.append(ch)

    # -- What This Booklet Is --
    ch = make_chapter("What This Booklet Is", """
        <h1>What This Booklet Is</h1>
        <p>You've just done this lesson — on the floor, in a class, or from a recording.
        This booklet is your companion afterward. It won't teach you the movements
        (you've already done them). Instead, it helps you understand what was happening
        underneath: why the teacher chose those movements in that order, what your
        nervous system was sorting out, and what to notice next time.</p>
        <p>Each Feldenkrais lesson is a carefully designed puzzle for your nervous system.
        The movements aren't exercises — they're questions. This booklet helps you
        see what questions were being asked, and what your body was discovering.</p>
    """)
    chapters.append(ch)

    # -- Where You Started --
    if home:
        ch = make_chapter("Where You Started", f"""
            <h1>Where You Started</h1>
            <div class="home-position"><p>{home}</p></div>
            <p>This starting position isn't arbitrary. It's chosen to give your nervous system
            a baseline — a clear sense of how things are <em>before</em> the lesson begins.
            You'll return to it at the end, and the contrast is often striking.</p>
        """)
        chapters.append(ch)

    # -- The Question --
    if somatic:
        body = '<h1>The Question This Lesson Asks</h1>'
        body += '<p>Every Feldenkrais lesson poses a question to your nervous system. Not a question you answer with words — a question you answer with movement, sensation, and gradually shifting organization.</p>'
        if somatic.get("primary_inquiry"):
            body += f'<blockquote>{somatic["primary_inquiry"]}</blockquote>'
        if somatic.get("washing_gradient"):
            body += f'<p>{somatic["washing_gradient"]}</p>'
        chapters.append(make_chapter("The Question", body))

    # -- How Feldenkrais Lessons Work --
    ch = make_chapter("How Lessons Work", """
        <h1>How Feldenkrais Lessons Work</h1>
        <p>To make sense of the journey through this lesson, it helps to understand three ideas.
        They'll come up throughout the booklet.</p>

        <h2>The Shadow</h2>
        <p>When you first try a movement, there's a part of you that grips, pushes, and
        forces. This is your voluntary muscular effort — we'll call it the <strong>Shadow</strong>.
        It means well, but it's noisy. It uses ten muscles when two would do. It holds
        your breath. It treats every movement as a task to be conquered.</p>

        <h2>The Foundation</h2>
        <p>Underneath the Shadow, your skeleton and reflexes already know how to support you.
        This deeper system — the <strong>Foundation</strong> — is quiet, efficient, and reflexive.
        It responds to gravity, finds balance, and transmits force through bone rather
        than muscle. The lesson's job is to let the Foundation emerge.</p>

        <h2>The Washing</h2>
        <p>The lesson doesn't fight the Shadow. It <em>washes</em> it away — through
        repetition, variation, and gentle complexity. Like water over stone, the
        unnecessary effort gradually dissolves. You don't try to relax. You simply
        repeat and vary until the Shadow runs out of strategies and the Foundation
        is all that remains.</p>

    """)
    chapters.append(ch)

    # -- The Journey (one chapter per group) --
    state_colors = {"bracing": "#c0392b", "softening": "#e67e22", "transmission": "#27ae60",
                     "domination": "#c0392b", "negotiation": "#e67e22", "integration": "#27ae60"}
    state_display = {"domination": "Bracing", "negotiation": "Softening", "integration": "Transmission"}

    for g in groups:
        gn = g.get("group_number", "?")
        label = g.get("label", f"Group {gn}")
        desc = g.get("description", "")
        g_step_nums = g.get("steps", [])

        body = f'<h1>Part {gn}: {label}</h1>'
        if desc:
            body += f'<p style="font-style: italic; color: #555;">{desc}</p>'

        # Steps
        body += '<h2>What You Did</h2><ol>'
        for sn in g_step_nums:
            step = step_lookup.get(sn)
            if step:
                inst = step.get("instruction", "")
                home_tag = " <em>(return to start)</em>" if step.get("is_home") else ""
                body += f"<li>{inst}{home_tag}</li>"
        body += "</ol>"

        # SPIFFER hints
        from collections import Counter
        all_tags = []
        for sn in g_step_nums:
            step = step_lookup.get(sn)
            if step:
                all_tags.extend(step.get("tags", []))
        if all_tags:
            tag_counts = Counter(all_tags)
            prominent = [t for t, c in tag_counts.most_common(3) if c > 1 or len(g_step_nums) <= 3]
            if prominent:
                for tag in prominent:
                    if tag in SPIFFER:
                        body += f'<p class="spiffer-hint">{SPIFFER[tag]}</p>'

        # Analytical table insights
        matched = group_rows.get(gn, [])
        if matched:
            body += '<h2>What Was Happening</h2>'
            for row in matched:
                neg = get_neg_state(row, col_headers)
                question = get_col_by_keyword(row, col_headers, "question")
                goal = get_col_by_keyword(row, col_headers, "goal") or get_col_by_keyword(row, col_headers, "functional")
                vl_voice = row.get("vl_voice", "")
                shadow_obs = row.get("shadow_voice", "")

                if neg:
                    color = state_colors.get(neg.lower(), "#777")
                    display = state_display.get(neg.lower(), neg)
                    body += f'<span class="neg-badge" style="background: {color};">{display}</span>'
                if question:
                    body += f'<p><strong>The question being posed:</strong> {question}</p>'
                if goal:
                    body += f'<p><strong>The functional goal:</strong> {goal}</p>'
                if vl_voice:
                    body += f'<div class="voice-box person-inside"><div class="voice-label">The Person Inside</div><p>{vl_voice}</p></div>'
                if shadow_obs:
                    body += f'<div class="voice-box shadow-voice"><div class="voice-label">The Shadow</div><p>{shadow_obs}</p></div>'

        chapters.append(make_chapter(f"Part {gn}", body))

    # -- The Arc --
    body = '<h1>The Arc</h1>'
    body += '<p>Every Feldenkrais lesson has a trajectory — from effort to ease, from noise to quiet. Here\'s how this lesson made that journey.</p>'
    if somatic.get("washing_gradient"):
        body += f'<h2>How the Lesson Washed Away Effort</h2><p>{somatic["washing_gradient"]}</p>'
    if somatic.get("threshold_of_quiet"):
        body += f'<h2>The Threshold of Quiet</h2><p>{somatic["threshold_of_quiet"]}</p>'
    if negotiation.get("shadow_observations"):
        body += '<h2>What the Shadow Was Doing</h2><ul>'
        for obs in negotiation["shadow_observations"]:
            body += f"<li>{obs}</li>"
        body += "</ul>"
    if negotiation.get("foundation_observations"):
        body += '<h2>What the Foundation Discovered</h2><ul>'
        for obs in negotiation["foundation_observations"]:
            body += f"<li>{obs}</li>"
        body += "</ul>"
    chapters.append(make_chapter("The Arc", body))

    # -- The Lens --
    body = f'<h1>The Lens: {intro["name"]}</h1>'
    body += f'<p>{intro["lens"]}</p>'
    body += f'<h2>{intro["person_inside"]}</h2>'
    body += f'<p>{intro["person_description"]}</p>'
    body += '<h2>The Shadow</h2>'
    body += f'<p>{intro["shadow_description"]}</p>'
    chapters.append(make_chapter("The Lens", body))

    # -- Next Time --
    all_tags = set()
    for step in steps:
        all_tags.update(step.get("tags", []))

    body = '<h1>Next Time</h1>'
    body += '<p>The question this lesson asks doesn\'t have a final answer. Each time you do it, your nervous system finds a slightly different solution. Here are some things to pay attention to next time:</p><ul>'
    if "S" in all_tags:
        body += "<li>Notice the very first moment of sensing — before you move. What do you feel on the floor? What's the shape of your contact?</li>"
    if "R" in all_tags:
        body += "<li>Pay attention to the connections. When you move one part, what ripples through to distant parts?</li>"
    if "E" in all_tags:
        body += "<li>Each time you're asked to reduce effort, see how little you can do and still have the movement happen.</li>"
    if "Q" in all_tags:
        body += "<li>When the teacher poses a question, resist the urge to find an answer. Let the question sit.</li>"
    if "P" in all_tags:
        body += "<li>Don't rush through the pauses. That's where the real work happens — your nervous system integrating what it just learned.</li>"
    body += '</ul><p style="text-align: center; font-style: italic; margin-top: 2em;">The lesson is a conversation between you and your nervous system. Each time you listen a little more carefully, you hear something new.</p>'
    chapters.append(make_chapter("Next Time", body))

    # -- Assemble --
    for ch in chapters:
        book.add_item(ch)

    # Table of contents
    book.toc = chapters[1:]  # skip title page
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    # Spine (reading order)
    book.spine = ["nav"] + chapters

    epub.write_epub(output_path, book, {})
    print(f"Generated: {output_path}")
    print(f"  Title: {title}")
    print(f"  Chapters: {len(chapters)}")
    print(f"  Framework: {framework_id} ({intro['name']})")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    doc_id = sys.argv[1]
    output = sys.argv[2] if len(sys.argv) > 2 else "booklet.epub"

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set in .env")
        sys.exit(1)

    db = Database(db_url)
    db.initialize()

    # Load lesson text to compute cache key
    text = db.get_document_transcription(doc_id)
    if not text:
        print(f"ERROR: No transcription found for document {doc_id}")
        sys.exit(1)

    cache_key = hashlib.sha256(text.strip().encode()).hexdigest()[:16]
    cached = db.get_cache(cache_key)
    if not cached:
        print(f"ERROR: No cached analysis for document {doc_id} (cache key: {cache_key})")
        sys.exit(1)

    # Get lesson title from DB
    with db.get_cursor(commit=False) as cur:
        cur.execute("SELECT title FROM document WHERE id = %s", (doc_id,))
        row = cur.fetchone()
        lesson_title = row["title"] if row else "Untitled"

    generate_epub(cached, lesson_title, output)


if __name__ == "__main__":
    main()
