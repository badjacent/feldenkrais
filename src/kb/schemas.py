"""
Structured output schemas for Feldenkrais lesson analysis (VL-1 v2.0).
"""

from pydantic import BaseModel, Field
from typing import Optional, Literal


class SPIFFERQStep(BaseModel):
    """A single step tagged with SPIFFER-Q attributes."""
    step_number: int
    instruction: str
    tags: list[str] = Field(description="SPIFFER-Q tags: S, P, I, F (Function), F (Focus), E, R, Q")
    notes: Optional[str] = None


class Phase1SPIFFERQ(BaseModel):
    """Phase 1: SPIFFER-Q Tagging - deconstruct text into discrete tagged steps."""
    steps: list[SPIFFERQStep]
    summary: Optional[str] = None


class Phase2Negotiation(BaseModel):
    """Phase 2: The Negotiation Framework between Striver and Foundation."""
    striver_observations: list[str] = Field(
        description="How the Postural Effort System (The Striver) manifests - cortical, voluntary, goal-oriented"
    )
    foundation_observations: list[str] = Field(
        description="How the Postural Support System (The Foundation) responds - subcortical, reflexive, seeks equilibrium"
    )
    washing_gradient: str = Field(
        description="How the Striver is gradually eroded through repetition, oscillation, or complexity"
    )


class SomaticNarrative(BaseModel):
    """The Somatic Narrative - linear chunks describing the lesson's arc."""
    primary_inquiry: str = Field(
        description="The fundamental neurological riddle of the lesson"
    )
    washing_gradient: str = Field(
        description="How the Striver is gradually eroded (through speed, complexity, or unusual configurations)"
    )
    threshold_of_quiet: str = Field(
        description="The moment where the Foundation takes over and the Vertical Self emerges"
    )


NegotiationState = Literal["Domination", "Negotiation", "Integration"]


class VL1TableRow(BaseModel):
    """A single row in the VL-1 Analytical Table."""
    step: str = Field(description="The physical action")
    spiffer_q_tags: list[str] = Field(description="Relevant SPIFFER-Q tags for this step")
    the_question: str = Field(description="The specific neurological riddle of this step")
    negotiation_state: NegotiationState = Field(
        description="Domination (Striver in control), Negotiation (Washing in progress), or Integration (Foundation has taken over)"
    )
    functional_goal: str = Field(description="Intended structural outcome")


class VL1Table(BaseModel):
    """The VL-1 Analytical Table tracking the negotiation through the lesson.

    Lessons generally fall into logical sections based on the movements involved.
    Each distinct movement or instruction should have its own row. There is no
    exact limit, but aim to capture each meaningful transition in the lesson,
    including the final return to initial position.
    """
    rows: list[VL1TableRow]


class LessonAnalysis(BaseModel):
    """Complete structured analysis of a Feldenkrais lesson (VL-1 v2.0)."""
    lesson_title: Optional[str] = None
    phase1_spifferq: Phase1SPIFFERQ
    phase2_negotiation: Phase2Negotiation
    phase3_somatic_narrative: SomaticNarrative
    phase3_table: VL1Table
