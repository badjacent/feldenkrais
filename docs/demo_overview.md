# Feldenkrais Lesson Search: What It Does

## The Idea

You have lesson recordings, videos, or transcripts. This system turns them into a searchable, browsable library where practitioners and students can find lessons by *meaning* — not just keywords — and explore each lesson's structure. This matters because finding the right lesson is hard: libraries grow, titles are cryptic, and students often know what they're looking for in terms of movement or sensation but not by name.

## How Lessons Get In

You provide a lesson transcript, audio recording, or video. The system handles the rest:

1. **Transcription** — If you provide audio, the system transcribes it with word-level timestamps, so the text stays synced to playback. If you have video, we extract the audio from the video.

2. **Analysis** — An LLM reads the transcript and breaks it into movement steps, grouping them into phases. Each step is analyzed for body mechanics — which muscles activate, what's being differentiated, how force travels through the body, where attention is directed. The teaching voice itself is also analyzed: imagery, attention cues, and where inquiry happens in the language.

3. **Search optimization** — A final extraction step reads the full analysis and predigests it to optimize for the capabilities of modern semantic search. It produces a detailed physical description of the lesson focused on what makes it distinctive, along with structured concept tags for body regions, movement patterns, and teaching strategies. Despite the apparent depth of processing, this step costs pennies per lesson and only needs to be done once.

All of this runs automatically. Results are cached, so you only pay the LLM cost once per lesson.

## Search

Search combines what is distinctive about a lesson, its movement structure, and the concepts it contains to generate a ranking. When the system is confident in a match, it commits to it; when results are ambiguous, it blends signals from multiple methods. Results include a specificity indicator showing whether a query is broad or precise.

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

## Search Quality

We evaluated several ranking strategies — embedding search against lesson summaries, embedding search against distinctive reports, direct concept tag matching, and various weighted combinations — then optimized a hybrid approach using a systematic grid search over parameters. This isn't guesswork: strategies are scored against a curated question set and the best combination is selected automatically. Running search queries is very cheap — less than a penny per hundred requests. As the library grows and real user queries accumulate, both the evaluation data and the search quality improve.

## What This Means for Lesson Site Owners

- **Your lessons become findable.** Students can search your library by movement theme, body region, or teaching concept — not just by title.
- **Each lesson is richer.** The step-by-step breakdown with somatic and language analysis adds a layer of understanding that supports both practitioners studying pedagogy and students reviewing what they experienced.
- **Audio gets a companion.** If you have recordings, the synced player with structured cards makes lessons navigable — skip to a specific movement phase, see what's happening at any moment.
- **It's automatic.** No manual tagging or annotation. Add a transcript, and the system does the rest.
- **Search improves with scale.** The concept extraction and evaluation framework mean search quality is measurable and tunable as the library grows.

