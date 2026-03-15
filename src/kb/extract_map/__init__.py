"""Extraction + Mapping pipeline for Feldenkrais lesson analysis.

Two-stage pipeline:
  Stage 1 (Extraction): Extract the sequence of instructions from a transcript
  Stage 2 (Mapping): Map extracted instructions to canonical ontology terms
"""

from .pipeline import run_extraction, run_mapping, run_full_pipeline
