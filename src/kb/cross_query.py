"""Cross-query enrichment pass for VL analyses.

After all 5 VLs complete their independent Pass B analyses, this module
runs a second pass where each VL can ask up to `budget_max` focused
questions of other VLs, then produce an enriched analysis incorporating
the responses.

Pipeline shape:
    analysis_b:{VL-1..5} → cross_query:{VL-1..5} → analysis_merge

Each cross_query node depends on ALL 5 analysis_b nodes.  The 5
cross_query nodes run in parallel (they read from cache, not from
each other).
"""

import json
from dataclasses import dataclass, field
from typing import Optional


# ── Data structures ────────────────────────────────────────────────────


@dataclass
class CrossQueryRequest:
    source_vl: str          # e.g. "VL-1"
    target_vl: str          # e.g. "VL-2"
    question: str
    rationale: str          # why this VL wants to ask
    start_line: Optional[int] = None  # optional text range
    end_line: Optional[int] = None


@dataclass
class CrossQueryResponse:
    source_vl: str
    target_vl: str
    question: str
    response: str


@dataclass
class CrossQueryLog:
    source_vl: str
    requests: list[CrossQueryRequest] = field(default_factory=list)
    responses: list[CrossQueryResponse] = field(default_factory=list)
    budget_used: int = 0
    budget_max: int = 3


# ── JSON schemas for structured LLM output ─────────────────────────────

CROSS_QUERY_GENERATION_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "CrossQueryGeneration",
    "description": "Questions that one VL wants to ask of other VLs to deepen its analysis",
    "type": "object",
    "properties": {
        "queries": {
            "type": "array",
            "description": "0 to budget_max questions for peer VLs. Empty array if no cross-queries needed.",
            "items": {
                "type": "object",
                "properties": {
                    "target_vl": {
                        "type": "string",
                        "enum": ["VL-1", "VL-2", "VL-3", "VL-4", "VL-5"],
                        "description": "Which VL to ask"
                    },
                    "question": {
                        "type": "string",
                        "description": "A focused question for the target VL about a specific aspect of the lesson"
                    },
                    "rationale": {
                        "type": "string",
                        "description": "Why this question would deepen your analysis"
                    },
                    "start_line": {
                        "type": ["integer", "null"],
                        "description": "Optional start line of the text range to focus on (null for whole lesson)"
                    },
                    "end_line": {
                        "type": ["integer", "null"],
                        "description": "Optional end line of the text range to focus on (null for whole lesson)"
                    }
                },
                "required": ["target_vl", "question", "rationale"]
            },
            "maxItems": 3
        }
    },
    "required": ["queries"]
}


# ── Helpers ─────────────────────────────────────────────────────────────


def _web():
    """Lazy import of web module."""
    try:
        import web as m
    except ImportError:
        from src.kb import web as m
    return m


def build_peer_directory(frameworks: list[dict]) -> str:
    """Build a markdown directory of what each VL specializes in.

    Included in the question-generation prompt so the VL knows who to ask.
    Only includes primary characters (not the shadow/Blob).
    """
    lines = ["# Peer VL Directory\n"]
    for char in frameworks:
        if char.get("role") != "primary":
            continue
        essence = char.get("essence", {})
        lines.append(f"## {char['id']}: {char.get('name', '')}")
        lines.append(f"- **Sees:** {essence.get('sees', '')[:200]}...")
        lines.append(f"- **Core verb:** {essence.get('core_verb', '')}")
        lines.append(f"- **Core distinction:** {essence.get('core_distinction', '')}")
        lines.append("")
    return "\n".join(lines)


def _summarize_analysis(vl_id: str, result: dict) -> str:
    """Create a brief summary of a VL's first-pass analysis for peer review."""
    narrative = result.get("somatic_narrative", {})
    negotiation = result.get("negotiation_framework", {})

    parts = [f"### {vl_id} Summary"]

    inquiry = narrative.get("primary_inquiry", "")
    if inquiry:
        parts.append(f"**Primary inquiry:** {inquiry}")

    gradient = narrative.get("washing_gradient", "")
    if gradient:
        parts.append(f"**Washing gradient:** {gradient[:300]}")

    threshold = narrative.get("threshold_of_quiet", "")
    if threshold:
        parts.append(f"**Threshold of quiet:** {threshold[:200]}")

    shadow = negotiation.get("shadow_observations", [])
    if shadow:
        parts.append(f"**Shadow observations:** {'; '.join(shadow[:3])}")

    foundation = negotiation.get("foundation_observations", [])
    if foundation:
        parts.append(f"**Foundation observations:** {'; '.join(foundation[:3])}")

    return "\n".join(parts)


def _get_text_slice(lesson_text: str, start_line: Optional[int], end_line: Optional[int]) -> str:
    """Extract a line range from lesson text, or return the full text."""
    if start_line is None and end_line is None:
        return lesson_text
    lines = lesson_text.split("\n")
    start = max(0, (start_line or 1) - 1)
    end = min(len(lines), end_line or len(lines))
    return "\n".join(lines[start:end])


# ── Core functions ──────────────────────────────────────────────────────


def generate_cross_queries(
    llm,
    source_vl_id: str,
    source_result: dict,
    all_results: dict[str, dict],
    peer_directory: str,
    lesson_text: str,
    budget_max: int = 3,
) -> list[CrossQueryRequest]:
    """Ask a VL to generate 0-N cross-query questions after seeing peer summaries.

    One LLM call (complete_structured_json). Returns a list of CrossQueryRequest.
    """
    if budget_max <= 0:
        return []

    # Build peer summaries (exclude self)
    peer_summaries = []
    for vl_id, result in sorted(all_results.items()):
        if vl_id != source_vl_id and result:
            peer_summaries.append(_summarize_analysis(vl_id, result))

    own_summary = _summarize_analysis(source_vl_id, source_result)

    # Set maxItems dynamically
    schema = json.loads(json.dumps(CROSS_QUERY_GENERATION_SCHEMA))
    schema["properties"]["queries"]["maxItems"] = budget_max

    prompt = f"""You are {source_vl_id}, reviewing your first-pass analysis of a Feldenkrais lesson.

## Your Analysis
{own_summary}

## Peer Analyses
{chr(10).join(peer_summaries)}

## Peer Directory (who to ask what)
{peer_directory}

## Task
Review your own analysis alongside what the other VLs found. Consider whether
another VL's perspective could deepen or clarify a specific aspect of YOUR analysis.

Generate 0 to {budget_max} focused questions for peer VLs. Each question should:
- Target a specific VL whose lens would illuminate something you noticed but couldn't fully explore
- Be about a specific moment or pattern in the lesson (not a general question)
- Include a rationale explaining why this peer's perspective would help YOUR analysis
- Optionally specify a line range (start_line, end_line) if the question is about a specific passage

Return an empty queries array if your analysis is already complete and no cross-VL
perspective would meaningfully improve it. Quality over quantity.

Do NOT ask your own VL. Do NOT ask the Blob/shadow."""

    system = "You are a Feldenkrais analysis assistant generating cross-VL queries."

    result = llm.complete_structured_json(prompt, schema, system=system)

    requests = []
    for q in result.get("queries", []):
        # Skip self-queries
        if q["target_vl"] == source_vl_id:
            continue
        requests.append(CrossQueryRequest(
            source_vl=source_vl_id,
            target_vl=q["target_vl"],
            question=q["question"],
            rationale=q["rationale"],
            start_line=q.get("start_line"),
            end_line=q.get("end_line"),
        ))

    return requests[:budget_max]


def execute_cross_query(
    llm,
    request: CrossQueryRequest,
    target_char: dict,
    lesson_text: str,
) -> CrossQueryResponse:
    """Execute a single cross-query: send a question to a target VL and get a response.

    One cheap LLM call (complete) per question. Returns 3-5 sentences through
    the target VL's lens.
    """
    w = _web()
    char_prompt = w._format_character_as_prompt(target_char)
    text_slice = _get_text_slice(lesson_text, request.start_line, request.end_line)

    text_context = ""
    if request.start_line or request.end_line:
        text_context = f"\n\n## Relevant Passage (lines {request.start_line or 1}-{request.end_line or '?'})\n{text_slice}"
    else:
        text_context = f"\n\n## Lesson Text\n{text_slice}"

    prompt = f"""{char_prompt}

---

{request.source_vl} is asking you ({request.target_vl}) the following question about a Feldenkrais lesson:

**Question:** {request.question}

**Why they're asking:** {request.rationale}
{text_context}

Respond in 3-5 sentences through your lens as {target_char.get('name', request.target_vl)}.
Focus specifically on what {request.source_vl} is asking about. Be concrete about
moments in the lesson, not abstract."""

    system = f"You are {target_char.get('name', request.target_vl)}, responding to a peer VL's question about a Feldenkrais lesson."

    response_text = llm.complete(prompt, system=system)

    return CrossQueryResponse(
        source_vl=request.source_vl,
        target_vl=request.target_vl,
        question=request.question,
        response=response_text,
    )


def enrich_analysis(
    llm,
    source_vl_id: str,
    source_char: dict,
    source_result: dict,
    cross_responses: list[CrossQueryResponse],
    lesson_text: str,
    pass_b_schema: dict,
) -> dict:
    """Produce an enriched analysis incorporating cross-query responses.

    One LLM call (complete_structured_json). Takes original analysis +
    cross-query responses, produces enriched analysis using same Pass B schema.
    """
    w = _web()
    char_prompt = w._format_character_as_prompt(source_char)

    # Format cross-query exchanges
    exchanges = []
    for resp in cross_responses:
        exchanges.append(
            f"**You asked {resp.target_vl}:** {resp.question}\n"
            f"**{resp.target_vl} responded:** {resp.response}"
        )
    exchanges_text = "\n\n".join(exchanges) if exchanges else "(No cross-queries were made.)"

    prompt = f"""{char_prompt}

---

## Original Lesson Text
{lesson_text}

---

## Your First-Pass Analysis
{json.dumps(source_result, indent=2)}

---

## Cross-VL Insights
You asked other VLs for their perspective on specific aspects of this lesson.
Here are their responses:

{exchanges_text}

---

## Task
Produce an enriched version of your analysis that incorporates insights from
the cross-VL responses. You should:

1. Keep your core analysis intact — you are still {source_vl_id}
2. Weave in relevant insights from peer VLs where they deepen YOUR perspective
3. You may refine your somatic_narrative and negotiation_framework observations
4. Update analytical_table rows where a peer insight illuminates a moment you already noted
5. Do NOT adopt another VL's lens — translate their insights into YOUR framework's terms

Output the complete analysis in the same schema as before."""

    system = w.ANALYSIS_SYSTEM_PROMPT

    return llm.complete_structured_json(prompt, pass_b_schema, system=system)


def run_cross_query_pass(
    llm,
    vl_id: str,
    db,
    lesson_text: str,
    frameworks: list[dict],
    pass_b_schema: dict,
    budget_max: int = 3,
) -> dict:
    """Orchestrate the full cross-query enrichment for one VL.

    1. Load all first-pass results from cache
    2. Generate cross-queries (0 to budget_max)
    3. Execute each query against the target VL
    4. Produce enriched analysis
    5. Write results to cache

    Returns the enriched result dict (or original if budget_max=0 or no queries generated).
    """
    # Load source VL's first-pass result
    source_result = db.get_cache(f"pipeline:analysis_b:{vl_id}")
    if not source_result:
        raise ValueError(f"No first-pass result for {vl_id}")

    # Load all first-pass results
    all_results = {}
    for char in frameworks:
        if char.get("role") != "primary":
            continue
        vid = char["id"]
        result = db.get_cache(f"pipeline:analysis_b:{vid}")
        if result:
            all_results[vid] = result

    # Find source character definition
    source_char = next((c for c in frameworks if c["id"] == vl_id), None)
    if not source_char:
        raise ValueError(f"No character definition for {vl_id}")

    # Build peer directory
    peer_directory = build_peer_directory(frameworks)

    # Initialize log
    log = CrossQueryLog(source_vl=vl_id, budget_max=budget_max)

    # Budget 0 → skip entirely, preserve first-pass
    if budget_max <= 0:
        log_dict = {
            "source_vl": log.source_vl,
            "budget_used": 0,
            "budget_max": 0,
            "queries": [],
        }
        db.set_cache(f"pipeline:cross_query_log:{vl_id}", log_dict)
        return source_result

    # Step 1: Generate cross-queries
    print(f"  [{vl_id}] Generating cross-queries (budget={budget_max})...", flush=True)
    requests = generate_cross_queries(
        llm, vl_id, source_result, all_results,
        peer_directory, lesson_text, budget_max,
    )
    log.requests = requests
    log.budget_used = len(requests)

    if not requests:
        print(f"  [{vl_id}] No cross-queries needed — preserving first-pass analysis.", flush=True)
        log_dict = {
            "source_vl": log.source_vl,
            "budget_used": 0,
            "budget_max": budget_max,
            "queries": [],
        }
        db.set_cache(f"pipeline:cross_query_log:{vl_id}", log_dict)
        return source_result

    # Step 2: Execute cross-queries
    responses = []
    for req in requests:
        target_char = next((c for c in frameworks if c["id"] == req.target_vl), None)
        if not target_char:
            print(f"  [{vl_id}] Warning: no character for {req.target_vl}, skipping.", flush=True)
            continue
        print(f"  [{vl_id}] Asking {req.target_vl}: {req.question[:80]}...", flush=True)
        resp = execute_cross_query(llm, req, target_char, lesson_text)
        responses.append(resp)
    log.responses = responses

    # Step 3: Enrich analysis
    print(f"  [{vl_id}] Enriching analysis with {len(responses)} cross-VL responses...", flush=True)
    enriched = enrich_analysis(
        llm, vl_id, source_char, source_result,
        responses, lesson_text, pass_b_schema,
    )

    # Step 4: Write enriched result (overwrites first-pass)
    db.set_cache(f"pipeline:analysis_b:{vl_id}", enriched)

    # Step 5: Write log
    log_dict = {
        "source_vl": log.source_vl,
        "budget_used": log.budget_used,
        "budget_max": log.budget_max,
        "queries": [
            {
                "target_vl": req.target_vl,
                "question": req.question,
                "rationale": req.rationale,
                "response_length": len(resp.response) if i < len(responses) else 0,
            }
            for i, req in enumerate(requests)
        ],
    }
    db.set_cache(f"pipeline:cross_query_log:{vl_id}", log_dict)

    print(f"  [{vl_id}] Cross-query enrichment complete ({log.budget_used} queries used).", flush=True)
    return enriched
