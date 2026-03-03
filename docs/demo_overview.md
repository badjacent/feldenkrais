# Feldenkrais Lesson Search: What It Does

## The Idea

You have lesson recordings, videos, or transcripts. This system turns them into a searchable, browsable library where practitioners and students can find lessons by *meaning* — not just keywords — and explore each lesson's structure. This matters because finding the right lesson is hard: libraries grow, titles are cryptic, and students often know what they're looking for in terms of movement or sensation but not by name.

## How Lessons Get In

You provide a lesson transcript, audio recording, or video. The system handles the rest:

1. **Transcription** — If you provide audio, the system transcribes it with word-level timestamps, so the text stays synced to playback. If you have video, we extract the audio from the video.

2. **Analysis** — An LLM (Large Language Model, similar to ChatGPT) reads the transcript and breaks it into movement steps, grouping them into phases. Each step is analyzed for body mechanics — which muscles activate, what's being differentiated, how force travels through the body, where attention is directed. The teaching voice itself is also analyzed: imagery, attention cues, and where inquiry happens in the language.

3. **Search optimization** — A final extraction step reads the full analysis and predigests it to optimize for the capabilities of modern semantic search. It produces a detailed physical description of the lesson focused on what makes it distinctive, along with structured concept tags for body regions, movement patterns, and teaching strategies. Despite the apparent depth of processing, this step costs pennies per lesson and only needs to be done once.

## Search

Search combines what is distinctive about a lesson, its movement structure, and the concepts it contains to generate a ranking. When the system is confident in a match, it commits to it; when results are ambiguous, it blends signals from multiple methods. Results include a specificity indicator showing whether a query is broad or precise.

We evaluated multiple ranking strategies and optimized a hybrid approach using systematic parameter tuning — not guesswork. Search queries cost less than a penny per hundred requests, and the system improves as the library grows and real user queries accumulate. [More about our search methodology.](/docs/search_subdomain)

## Lesson Presentation

We can't present a lesson like a real teacher would, but we can give the user a structured breakdown of what happens in the lesson and integrate it with an audio player for the original recording. Each lesson gets a detail page with three views:

- **Outline** — The lesson's arc at a glance: movement groups, step summaries, positions, somatic themes
- **Cards** — One step at a time, synced to audio playback if available. Each card shows the instruction, body analysis, and language notes. Navigate forward/back or let the audio drive.
- **Detailed** — Full text-based view of every step with complete analysis, organized by movement group

If the lesson has audio, an integrated player synced to the step-by-step breakdown gives another way of experiencing a lesson in the imagination.

### Lesson Chat

Each lesson page includes an AI chat panel. A practitioner or student can ask questions grounded in the full analysis:

- "What muscles are activated in step 3?"
- "How does the teacher use imagery in this lesson?"
- "What's the progression of positions across the lesson?"



## What This Means for Lesson Site Owners

- **Your lessons become findable.** Students can search your library by movement theme, body region, or teaching concept — not just by title.
- **Each lesson is richer.** The step-by-step breakdown with somatic and language analysis adds a layer of understanding that supports both practitioners studying pedagogy and students reviewing what they experienced.
- **Audio gets a companion.** If you have recordings, the synced player with structured cards makes lessons navigable — skip to a specific movement phase, see what's happening at any moment.
- **It's automatic.** No manual tagging or annotation. Add a transcript, and the system does the rest.
- **Search improves with scale.** The concept extraction and evaluation framework mean search quality is measurable and tunable as the library grows.

