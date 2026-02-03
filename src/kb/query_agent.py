"""
General-purpose agent that fills <div class="llm-query"> blocks in HTML documents.

The agent extracts query blocks from HTML, generates text for each using an LLM,
runs a self-review pass for coherence, and revises flagged blocks.

Usage:
    from kb.llm import LLM
    from kb.query_agent import QueryAgent

    agent = QueryAgent(llm=LLM(provider="gemini", model="gemini-2.0-flash"))
    filled_html = agent.fill(intermediate_html)

CLI:
    uv run python -m src.kb.query_agent --input booklet.html --output filled.html --provider gemini
    uv run python -m src.kb.query_agent --input booklet.html --dry-run
"""

import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Optional

try:
    from llm import LLM
except ImportError:
    from .llm import LLM


@dataclass
class QueryBlock:
    """A single query block extracted from HTML."""
    block_id: str           # positional ID: "q-0", "q-1", ...
    query_type: str         # from data-query-type
    context: dict           # parsed data-context JSON
    prompt: str             # text from .query-prompt paragraph
    original_html: str      # the full <div>...</div> for replacement


@dataclass
class Revision:
    """A review-suggested revision for a filled block."""
    block_id: str
    feedback: str


class _QueryBlockParser(HTMLParser):
    """Extract llm-query divs from HTML.

    Finds <div class="llm-query" ...> blocks and extracts their
    data-query-type, data-context, and inner prompt text.
    """

    def __init__(self):
        super().__init__()
        self.blocks: list[QueryBlock] = []
        self._in_query_div = False
        self._depth = 0
        self._current_attrs: dict = {}
        self._current_text: list[str] = []
        self._current_html_parts: list[str] = []
        self._block_count = 0

    def handle_starttag(self, tag, attrs):
        attr_dict = dict(attrs)
        classes = attr_dict.get("class", "").split()

        if tag == "div" and "llm-query" in classes:
            self._in_query_div = True
            self._depth = 1
            self._current_attrs = attr_dict
            self._current_text = []
            self._current_html_parts = [self.get_starttag_text() or ""]
            return

        if self._in_query_div:
            if tag == "div":
                self._depth += 1
            self._current_html_parts.append(self.get_starttag_text() or "")

    def handle_endtag(self, tag):
        if self._in_query_div:
            self._current_html_parts.append(f"</{tag}>")
            if tag == "div":
                self._depth -= 1
                if self._depth == 0:
                    self._finish_block()
                    return

    def handle_data(self, data):
        if self._in_query_div:
            self._current_text.append(data)
            self._current_html_parts.append(data)

    def handle_entityref(self, name):
        if self._in_query_div:
            ref = f"&{name};"
            self._current_text.append(ref)
            self._current_html_parts.append(ref)

    def handle_charref(self, name):
        if self._in_query_div:
            ref = f"&#{name};"
            self._current_text.append(ref)
            self._current_html_parts.append(ref)

    def _finish_block(self):
        self._in_query_div = False
        prompt_text = " ".join("".join(self._current_text).split()).strip()
        original_html = "".join(self._current_html_parts)

        context_raw = self._current_attrs.get("data-context", "{}")
        try:
            context = json.loads(context_raw)
        except (json.JSONDecodeError, TypeError):
            context = {}

        block = QueryBlock(
            block_id=f"q-{self._block_count}",
            query_type=self._current_attrs.get("data-query-type", "unknown"),
            context=context,
            prompt=prompt_text,
            original_html=original_html,
        )
        self.blocks.append(block)
        self._block_count += 1


@dataclass
class QueryAgent:
    """Fills <div class="llm-query"> blocks in HTML documents.

    Phases:
        1. Extract all query blocks from the HTML
        2. Generate text for each block via LLM
        3. Self-review the filled document for coherence
        4. Revise flagged blocks

    Caching is handled at the pipeline level, not here.
    """
    llm: LLM
    review_llm: Optional[LLM] = None
    max_review_cycles: int = 1
    verbose: bool = False

    def __post_init__(self):
        if self.review_llm is None:
            self.review_llm = self.llm

    def fill(self, html: str) -> str:
        """Fill all query blocks in the HTML. Returns modified HTML."""
        blocks = self._extract_blocks(html)
        if not blocks:
            if self.verbose:
                print("No query blocks found.")
            return html

        if self.verbose:
            print(f"Found {len(blocks)} query block(s).")

        # Phase 2: Generate fills
        filled: dict[str, str] = {}
        for block in blocks:
            if self.verbose:
                print(f"  Filling {block.block_id} ({block.query_type})...")
            filled[block.block_id] = self._generate(block)

        # Phase 3-4: Review and revise
        result_html = self._replace_blocks(html, blocks, filled)
        for cycle in range(self.max_review_cycles):
            revisions = self._review(result_html, blocks, filled)
            if not revisions:
                if self.verbose:
                    print(f"  Review cycle {cycle + 1}: no revisions needed.")
                break
            if self.verbose:
                print(f"  Review cycle {cycle + 1}: revising {len(revisions)} block(s).")
            block_lookup = {b.block_id: b for b in blocks}
            for rev in revisions:
                block = block_lookup.get(rev.block_id)
                if block and rev.block_id in filled:
                    filled[rev.block_id] = self._revise(
                        block, filled[rev.block_id], rev.feedback
                    )
            result_html = self._replace_blocks(html, blocks, filled)

        return result_html

    def fill_by_type(self, html: str, query_type: str) -> str:
        """Fill only blocks of a specific query type."""
        blocks = self._extract_blocks(html)
        target_blocks = [b for b in blocks if b.query_type == query_type]
        if not target_blocks:
            return html

        filled: dict[str, str] = {}
        for block in target_blocks:
            if self.verbose:
                print(f"  Filling {block.block_id} ({block.query_type})...")
            filled[block.block_id] = self._generate(block)

        return self._replace_blocks(html, blocks, filled)

    # -- internal methods --

    def _extract_blocks(self, html: str) -> list[QueryBlock]:
        """Parse HTML and extract all llm-query divs."""
        parser = _QueryBlockParser()
        parser.feed(html)
        return parser.blocks

    def _generate(self, block: QueryBlock) -> str:
        """Generate text for a single query block."""
        system = (
            "You are a skilled writer generating content for an inline section of a document. "
            "Write ONLY the requested text — no preamble, no explanation, no markdown formatting. "
            "The output will be inserted directly into an HTML document as paragraph text."
        )
        # Build prompt with context
        prompt_parts = [block.prompt]
        if block.context:
            prompt_parts.append(
                f"\n\nFull context (JSON):\n{json.dumps(block.context, indent=2)}"
            )
        prompt = "\n".join(prompt_parts)

        return self.llm.complete(prompt, system=system).strip()

    def _review(
        self, html: str, blocks: list[QueryBlock], filled: dict[str, str]
    ) -> list[Revision]:
        """Review filled blocks for coherence and quality."""
        if not filled:
            return []

        # Build a summary of filled blocks for review (avoid sending full HTML)
        summary_parts = []
        for block in blocks:
            if block.block_id in filled:
                summary_parts.append(
                    f"[{block.block_id}] (type: {block.query_type})\n"
                    f"Prompt: {block.prompt[:200]}...\n"
                    f"Generated: {filled[block.block_id]}"
                )
        if not summary_parts:
            return []

        review_prompt = (
            "You are reviewing AI-generated text sections that will be inserted into a document. "
            "Each section is identified by a block_id.\n\n"
            "Sections:\n" + "\n\n".join(summary_parts) + "\n\n"
            "Review for:\n"
            "1. Coherence: Do the sections flow naturally when read in sequence?\n"
            "2. Repetition: Are multiple sections saying essentially the same thing?\n"
            "3. Voice: Is the tone consistent throughout?\n"
            "4. Accuracy: Does each section reference specific content, not generic filler?\n\n"
            "Return revisions ONLY for sections that need improvement. "
            "If all sections are acceptable, return an empty revisions list."
        )

        review_schema = {
            "type": "object",
            "properties": {
                "revisions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "block_id": {"type": "string"},
                            "feedback": {"type": "string"},
                        },
                        "required": ["block_id", "feedback"],
                    },
                }
            },
            "required": ["revisions"],
        }

        try:
            result = self.review_llm.complete_structured_json(
                review_prompt, review_schema
            )
            return [
                Revision(block_id=r["block_id"], feedback=r["feedback"])
                for r in result.get("revisions", [])
                if r.get("block_id") in filled  # only valid block IDs
            ]
        except Exception as e:
            if self.verbose:
                print(f"  Review failed: {e}")
            return []

    def _revise(self, block: QueryBlock, original_text: str, feedback: str) -> str:
        """Revise a filled block based on review feedback."""
        system = (
            "You are revising a piece of AI-generated text based on editorial feedback. "
            "Write ONLY the revised text — no preamble, no explanation, no markdown formatting."
        )
        prompt = (
            f"Original prompt: {block.prompt}\n\n"
            f"Previously generated text: {original_text}\n\n"
            f"Feedback: {feedback}\n\n"
            f"Write a revised version that addresses the feedback."
        )
        if block.context:
            prompt += f"\n\nContext (JSON):\n{json.dumps(block.context, indent=2)}"

        return self.llm.complete(prompt, system=system).strip()

    def _replace_blocks(
        self, html: str, blocks: list[QueryBlock], filled: dict[str, str]
    ) -> str:
        """Replace query divs with filled content divs.

        Uses regex to find llm-query divs in order and replaces them
        sequentially, matching blocks by position rather than content.
        """
        import html as html_mod

        # Build replacements keyed by position (0-indexed order of appearance)
        replacements: dict[int, str] = {}
        for i, block in enumerate(blocks):
            if block.block_id in filled:
                text = filled[block.block_id]
                bid = html_mod.escape(block.block_id)
                qtype = html_mod.escape(block.query_type)
                prompt = html_mod.escape(block.prompt)
                replacements[i] = (
                    f'<div class="llm-filled-group" data-query-type="{qtype}" data-block-id="{bid}">'
                    f'<div class="llm-tab-bar">'
                    f'<button class="llm-tab active" data-show="response">Response</button>'
                    f'<button class="llm-tab" data-show="prompt">Prompt</button>'
                    f'</div>'
                    f'<div class="llm-filled llm-tab-content active" data-panel="response">'
                    f"<p>{text}</p>"
                    f"</div>"
                    f'<div class="llm-query llm-tab-content" data-panel="prompt" style="display:none;">'
                    f'<p class="query-prompt">{prompt}</p>'
                    f"</div>"
                    f"</div>"
                )

        if not replacements:
            return html

        # Match <div class="llm-query" ...>...</div> blocks (non-greedy, handles nested content)
        # Pattern: opening div with llm-query class, everything up to the matching </div>
        pattern = re.compile(
            r'<div\s[^>]*class="[^"]*llm-query[^"]*"[^>]*>.*?</div>',
            re.DOTALL,
        )

        match_index = 0
        def _replacer(m: re.Match) -> str:
            nonlocal match_index
            idx = match_index
            match_index += 1
            return replacements.get(idx, m.group(0))

        return pattern.sub(_replacer, html)


# -- CLI --

def main():
    import argparse
    import sys
    from dotenv import load_dotenv
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Fill LLM query blocks in HTML documents."
    )
    parser.add_argument("--input", "-i", help="Input HTML file path")
    parser.add_argument("--output", "-o", help="Output HTML file path (default: stdout)")
    parser.add_argument(
        "--provider",
        default="gemini",
        choices=["gemini", "openai", "anthropic"],
        help="LLM provider (default: gemini)",
    )
    parser.add_argument(
        "--review-provider",
        help="Separate LLM provider for review pass (default: same as --provider)",
    )
    parser.add_argument(
        "--review-cycles",
        type=int,
        default=1,
        help="Max review/revise cycles (default: 1)",
    )
    parser.add_argument(
        "--query-type",
        help="Only fill blocks of this query type",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Extract and display blocks without making LLM calls",
    )
    parser.add_argument("--verbose", "-v", action="store_true")

    args = parser.parse_args()

    if not args.input:
        parser.error("--input is required")

    # Read input
    with open(args.input) as f:
        html = f.read()

    if args.dry_run:
        parser_obj = _QueryBlockParser()
        parser_obj.feed(html)
        blocks = parser_obj.blocks
        print(f"Found {len(blocks)} query block(s):\n")
        for block in blocks:
            print(f"  {block.block_id}: type={block.query_type}")
            print(f"    prompt: {block.prompt[:120]}...")
            ctx_keys = list(block.context.keys()) if block.context else []
            print(f"    context keys: {ctx_keys}")
            print()
        return

    # Build LLMs
    provider_configs = {
        "gemini": {"provider": "gemini", "model": "gemini-2.0-flash", "default_max_tokens": 65536},
        "openai": {"provider": "openai", "model": "gpt-4o", "default_max_tokens": 128000},
        "anthropic": {"provider": "anthropic", "model": "claude-sonnet-4-20250514", "default_max_tokens": 16384},
    }

    llm = LLM(**provider_configs[args.provider])
    review_llm = None
    if args.review_provider:
        review_llm = LLM(**provider_configs[args.review_provider])

    agent = QueryAgent(
        llm=llm,
        review_llm=review_llm,
        max_review_cycles=args.review_cycles,
        verbose=args.verbose,
    )

    if args.query_type:
        result = agent.fill_by_type(html, args.query_type)
    else:
        result = agent.fill(html)

    if args.output:
        with open(args.output, "w") as f:
            f.write(result)
        print(f"Wrote filled HTML to {args.output}")
    else:
        sys.stdout.write(result)


if __name__ == "__main__":
    main()
