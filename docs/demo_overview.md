# Feldenkrais Lesson Search: What It Does

## The Idea

You have lesson recordings or transcripts. This system turns them into a searchable, browsable library where practitioners and students can find lessons by *meaning* — not just keywords — and explore each lesson's structure in detail.

## How Lessons Get In

You provide a lesson transcript (text file or audio recording). The system handles the rest:

1. **Audio transcription** — If you provide audio, the system transcribes it with word-level timestamps, so the text stays synced to playback.

2. **Structural analysis** — An LLM reads the transcript and breaks it into movement steps: what position the student is in, what they're asked to do, how steps group into phases. This is the backbone everything else builds on.

3. **Somatic analysis** — Each step gets analyzed for body mechanics: which muscles activate, what's being differentiated, how force travels through the body, where attention is directed.

4. **Language analysis** — The teaching voice itself is analyzed: how the teacher uses imagery, what kinds of attention cues appear, where inquiry and exploration happen in the language.

All of this runs automatically. Results are cached, so you only pay the LLM cost once per lesson.

## Search

Search queries run against the structured understanding of each lesson — not raw text. When someone searches for "lessons that work with the eyes and turning," the system matches against analyzed movement patterns, body regions, and teaching themes.

Results are ranked by semantic similarity, with a specificity indicator showing whether the query is broad ("many lessons match equally") or precise ("strong match, clear winner").

## Lesson Pages

Each lesson gets a detail page with three views:

- **Outline** — The lesson's arc at a glance: movement groups, step summaries, positions, somatic themes
- **Cards** — One step at a time, synced to audio playback if available. Each card shows the instruction, body analysis, and language notes. Navigate forward/back or let the audio drive.
- **Detailed** — Full text-based view of every step with complete analysis, organized by movement group

If the lesson has audio, an integrated player shows the transcript segment in real time with word-level highlighting.

## Lesson Chat

Each lesson page includes an AI chat panel. A practitioner or student can ask questions grounded in the full analysis:

- "What muscles are activated in step 3?"
- "How does the teacher use imagery in this lesson?"
- "What's the progression of positions across the lesson?"

The chat has access to the transcript, structural breakdown, somatic analysis, and language analysis — configurable per conversation.

## What This Means for Lesson Site Owners

- **Your lessons become findable.** Students can search your library by movement theme, body region, or teaching concept — not just by title.
- **Each lesson is richer.** The step-by-step breakdown with somatic and language analysis adds a layer of understanding that supports both practitioners studying pedagogy and students reviewing what they experienced.
- **Audio gets a companion.** If you have recordings, the synced player with structured cards makes lessons navigable — skip to a specific movement phase, see what's happening at any moment.
- **It's automatic.** No manual tagging or annotation. Add a transcript, and the system does the rest.

Currently running with 67 public lessons. The system handles new lessons in minutes.
