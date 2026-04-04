"""Backfill prompt IO cache entries for existing analyses.

Re-assembles the prompts that were sent to the LLM for each analysis step
using the current prompt templates + cached upstream data. Saves under
{cache_key}:prompt so the raw I/O UI can display them.

Usage:
    .venv/bin/python scripts/backfill_prompt_io.py [--dry-run] [--document DOC_ID]
"""

import argparse
import os
import sys
from pathlib import Path

# Add src/kb to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "kb"))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from database import Database
import cache_helpers
import llm_config
import analysis


# Step name -> cache suffix mapping (must match what the analysis code uses)
STEP_SUFFIXES = {
    "decompose": ":decompose_v3",
    "somatic": ":somatic_v2",
    "language": ":language_v1",
    "language_patterns": ":language_patterns_v3",
    "concept_tags": ":concept_tags_v2",
    "spatial": ":spatial_v2",
    "condense": ":condense_v4",
    "segmentation": ":segmentation_v2",
}

# System prompts (must match what the analysis code uses)
SYSTEM_PROMPTS = {
    "decompose": analysis.ROUTER_SYSTEM_PROMPT,
    "somatic": (
        "You are an expert Feldenkrais practitioner enriching lesson steps. "
        "Process every step. Do not skip, merge, or split steps."
    ),
    "language": (
        "You are an expert in Feldenkrais pedagogy and language analysis. "
        "Analyze the teacher's language choices at each step. "
        "Process every step. Do not skip, merge, or split steps."
    ),
    "language_patterns": (
        "You are an expert in Feldenkrais pedagogy and language analysis. "
        "Annotate the teacher's language at the word/phrase level. "
        "Process every step. Do not skip, merge, or split steps."
    ),
    "concept_tags": (
        "You are an expert Feldenkrais practitioner tagging a lesson with "
        "searchable concepts. Be specific and distinctive — avoid generic terms "
        "that apply to every lesson."
    ),
    "spatial": (
        "You are a factual observer describing the physical geometry of body "
        "positions and configurations. Report only what is stated — no "
        "interpretation, no biomechanical analysis, no pedagogical language."
    ),
    "segmentation": (
        "You are an expert Feldenkrais practitioner identifying pedagogical arcs in a lesson. "
        "Most lessons are a single arc. Only segment when there is a genuine shift in organizing principle. "
        "HARD CONSTRAINTS: Maximum 2 segments. The first segment must include at least the first four groups. "
        "Never split the early exploratory phase of a lesson into its own segment."
    ),
    "condense": (
        "You are an expert Feldenkrais practitioner creating condensed lesson cards. "
        "Preserve all five points of view. Generate group cards first, then the setting card."
    ),
}


def get_transcript(db, document_id):
    with db.get_cursor(commit=False) as cur:
        cur.execute(
            "SELECT data->>'text' as text FROM analysis WHERE document_id = %s AND type_id = 1",
            (document_id,),
        )
        row = cur.fetchone()
        return row["text"].strip() if row and row["text"] else None


def backfill_document(db, document_id, title, dry_run=False):
    transcript = get_transcript(db, document_id)
    if not transcript:
        print(f"  SKIP {document_id[:8]} — no transcript")
        return 0

    filled = 0

    # --- Pass A (decompose) ---
    pa_suffix = STEP_SUFFIXES["decompose"]
    pa_cache_key = cache_helpers.get_cache_key(transcript + pa_suffix)
    pa_data = cache_helpers.load_cached_analysis(pa_cache_key)

    if pa_data and not cache_helpers.load_cached_analysis(pa_cache_key + ":prompt"):
        # Re-assemble Pass A prompt
        pass_0_result = analysis.run_pass_0(transcript)
        segmented_text = analysis.format_text_with_segments(pass_0_result)
        pa_prompt_template, pa_schema = analysis.load_pass_a()
        if pa_prompt_template:
            prompt = pa_prompt_template + "\n\n---\n\n## Lesson to Analyze:\n\n" + segmented_text
            if dry_run:
                print(f"  WOULD save decompose prompt ({len(prompt)} chars)")
            else:
                cache_helpers.save_prompt_io(pa_cache_key, prompt, SYSTEM_PROMPTS["decompose"], pa_schema)
                print(f"  saved decompose prompt ({len(prompt)} chars)")
            filled += 1

    if not pa_data:
        print(f"  SKIP {title[:40]} — no Pass A data")
        return filled

    # --- Somatic ---
    som_suffix = STEP_SUFFIXES["somatic"]
    som_cache_key = cache_helpers.get_cache_key(transcript.strip() + som_suffix)
    som_data = cache_helpers.load_cached_analysis(som_cache_key)

    if som_data and not cache_helpers.load_cached_analysis(som_cache_key + ":prompt"):
        steps_text = analysis.format_steps_for_0b(pa_data)
        prompt_template, schema = analysis.load_pass_0b()
        prompt = prompt_template + steps_text
        if dry_run:
            print(f"  WOULD save somatic prompt ({len(prompt)} chars)")
        else:
            cache_helpers.save_prompt_io(som_cache_key, prompt, SYSTEM_PROMPTS["somatic"], schema)
            print(f"  saved somatic prompt ({len(prompt)} chars)")
        filled += 1

    # --- Language ---
    lang_suffix = STEP_SUFFIXES["language"]
    lang_cache_key = cache_helpers.get_cache_key(transcript.strip() + lang_suffix)
    lang_data = cache_helpers.load_cached_analysis(lang_cache_key)

    if lang_data and not cache_helpers.load_cached_analysis(lang_cache_key + ":prompt"):
        steps_text = analysis.format_steps_for_0b(pa_data)
        prompt_template, schema = analysis.load_pass_0b_lang()
        prompt = prompt_template + steps_text
        if dry_run:
            print(f"  WOULD save language prompt ({len(prompt)} chars)")
        else:
            cache_helpers.save_prompt_io(lang_cache_key, prompt, SYSTEM_PROMPTS["language"], schema)
            print(f"  saved language prompt ({len(prompt)} chars)")
        filled += 1

    # --- Language Patterns (word-level) ---
    lp_suffix = STEP_SUFFIXES["language_patterns"]
    lp_cache_key = cache_helpers.get_cache_key(transcript.strip() + lp_suffix)
    lp_data = cache_helpers.load_cached_analysis(lp_cache_key)

    if lp_data and lang_data and not cache_helpers.load_cached_analysis(lp_cache_key + ":prompt"):
        lang_enriched = lang_data.get("enriched_steps", [])
        steps_text = analysis.format_steps_for_word_lang(pa_data, lang_enriched)
        prompt_template, schema = analysis.load_pass_0b_word_lang()
        prompt = prompt_template + steps_text
        if dry_run:
            print(f"  WOULD save language_patterns prompt ({len(prompt)} chars)")
        else:
            cache_helpers.save_prompt_io(lp_cache_key, prompt, SYSTEM_PROMPTS["language_patterns"], schema)
            print(f"  saved language_patterns prompt ({len(prompt)} chars)")
        filled += 1

    # --- Concepts ---
    ct_suffix = STEP_SUFFIXES["concept_tags"]
    ct_cache_key = cache_helpers.get_cache_key(transcript.strip() + ct_suffix)
    ct_data = cache_helpers.load_cached_analysis(ct_cache_key)

    if ct_data and som_data and not cache_helpers.load_cached_analysis(ct_cache_key + ":prompt"):
        somatic_steps = som_data.get("enriched_steps", [])
        concept_input = analysis.format_input_for_concepts(pa_data, somatic_steps)
        prompt_template, schema = analysis.load_pass_0b_concepts()
        prompt = prompt_template + concept_input
        if dry_run:
            print(f"  WOULD save concept_tags prompt ({len(prompt)} chars)")
        else:
            cache_helpers.save_prompt_io(ct_cache_key, prompt, SYSTEM_PROMPTS["concept_tags"], schema)
            print(f"  saved concept_tags prompt ({len(prompt)} chars)")
        filled += 1

    # --- Spatial ---
    sp_suffix = STEP_SUFFIXES["spatial"]
    sp_cache_key = cache_helpers.get_cache_key(transcript.strip() + sp_suffix)
    sp_data = cache_helpers.load_cached_analysis(sp_cache_key)

    if sp_data and som_data and not cache_helpers.load_cached_analysis(sp_cache_key + ":prompt"):
        somatic_steps = som_data.get("enriched_steps", [])
        spatial_input = analysis.format_input_for_spatial(pa_data, somatic_steps)
        prompt_template, schema = analysis.load_pass_0b_spatial()
        prompt = prompt_template + spatial_input
        if dry_run:
            print(f"  WOULD save spatial prompt ({len(prompt)} chars)")
        else:
            cache_helpers.save_prompt_io(sp_cache_key, prompt, SYSTEM_PROMPTS["spatial"], schema)
            print(f"  saved spatial prompt ({len(prompt)} chars)")
        filled += 1

    # --- Segmentation ---
    seg_suffix = STEP_SUFFIXES["segmentation"]
    seg_cache_key = cache_helpers.get_cache_key(transcript.strip() + seg_suffix)
    seg_data = cache_helpers.load_cached_analysis(seg_cache_key)

    if seg_data and som_data and lang_data and not cache_helpers.load_cached_analysis(seg_cache_key + ":prompt"):
        somatic_summary = som_data.get("summary", {})
        lang_summary = lang_data.get("summary", {})
        seg_input = analysis.format_input_for_segmentation(pa_data, somatic_summary, lang_summary)
        prompt_template, schema = analysis.load_pass_0b_segmentation()
        prompt = prompt_template + seg_input
        if dry_run:
            print(f"  WOULD save segmentation prompt ({len(prompt)} chars)")
        else:
            cache_helpers.save_prompt_io(seg_cache_key, prompt, SYSTEM_PROMPTS["segmentation"], schema)
            print(f"  saved segmentation prompt ({len(prompt)} chars)")
        filled += 1

    # --- Condensed ---
    cond_suffix = STEP_SUFFIXES["condense"]
    cond_cache_key = cache_helpers.get_cache_key(transcript.strip() + cond_suffix)
    cond_data = cache_helpers.load_cached_analysis(cond_cache_key)

    if cond_data and som_data and lang_data and not cache_helpers.load_cached_analysis(cond_cache_key + ":prompt"):
        somatic_enriched = som_data.get("enriched_steps", [])
        somatic_summary = som_data.get("summary", {})
        lang_enriched = lang_data.get("enriched_steps", [])
        lang_summary = lang_data.get("summary", {})
        cond_input = analysis.format_input_for_0b_condensed(
            pa_data, somatic_enriched, somatic_summary, lang_enriched, lang_summary,
        )
        prompt_template, schema = analysis.load_pass_0b_condensed()
        prompt = prompt_template + cond_input
        if dry_run:
            print(f"  WOULD save condense prompt ({len(prompt)} chars)")
        else:
            cache_helpers.save_prompt_io(cond_cache_key, prompt, SYSTEM_PROMPTS["condense"], schema)
            print(f"  saved condense prompt ({len(prompt)} chars)")
        filled += 1

    return filled


def main():
    parser = argparse.ArgumentParser(description="Backfill prompt IO cache entries")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be saved without saving")
    parser.add_argument("--document", help="Only backfill a specific document ID")
    args = parser.parse_args()

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set")
        sys.exit(1)

    db = Database(db_url)
    cache_helpers.init(db)
    llm_config.init(db)

    data_dir = Path(__file__).parent.parent / "data"
    analysis.init(db, data_dir, llm_config.get_llm)

    def _get_db():
        _db = Database(db_url)
        cache_helpers.init(_db)
        return _db

    if args.document:
        doc = db.get_document(args.document)
        if not doc:
            print(f"Document not found: {args.document}")
            sys.exit(1)
        print(f"Backfilling: {doc['title']}")
        filled = backfill_document(db, args.document, doc["title"], args.dry_run)
        print(f"Done: {filled} prompts {'would be ' if args.dry_run else ''}saved")
    else:
        # Get all documents with completed analysis
        with db.get_cursor(commit=False) as cur:
            cur.execute("""
                SELECT DISTINCT d.id, d.title
                FROM document d
                JOIN document_analysis_state das ON d.id = das.document_id
                WHERE das.step_name = 'decompose' AND das.completed_version IS NOT NULL
                ORDER BY d.title
            """)
            docs = cur.fetchall()

        print(f"Found {len(docs)} documents with completed analysis")
        total = 0
        for i, doc in enumerate(docs):
            print(f"\n[{i+1}/{len(docs)}] {doc['title']}")
            try:
                filled = backfill_document(db, str(doc["id"]), doc["title"], args.dry_run)
                total += filled
            except Exception as e:
                print(f"  ERROR: {e} — reconnecting...")
                try:
                    db.close()
                except Exception:
                    pass
                db = _get_db()
                # Retry once after reconnect
                try:
                    filled = backfill_document(db, str(doc["id"]), doc["title"], args.dry_run)
                    total += filled
                except Exception as e2:
                    print(f"  RETRY FAILED: {e2} — skipping")

        print(f"\nTotal: {total} prompts {'would be ' if args.dry_run else ''}saved")

    db.close()


if __name__ == "__main__":
    main()
