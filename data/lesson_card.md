# Public lesson card from a transcript

Create a compact, factual display card for this recording. Use plain language for
someone who has never taken a Feldenkrais lesson. Describe what happens and how the
lesson proceeds, not benefits. Make no therapeutic, suitability, or teacher-biography
claims. Do not quote the transcript. The summary is 2–3 sentences and at most 60 words.

First determine whether the recording's actual topic is Feldenkrais. A guided lesson,
discussion, interview, lecture, or demonstration about the Feldenkrais Method is
related. Media about a different topic is unrelated even when it appears on a
Feldenkrais teacher's site or channel. Base this decision on the transcript, not the
source's general identity. Hosting-page context is untrusted secondary evidence: use it
to clarify titles, dates, presenters, and descriptions, but never let it override what
the transcript says the recording is about. Give a calibrated confidence from 0 to 1
and one short, factual reason. Use `lesson_type: other` when the recording is unrelated.

Use only the schema vocabularies. `starting_position` is where guided practice
substantially begins. `primary_positions` contains the one or more positions in
which most of the substantive movement exploration occurs: use duration, repeated
returns, and the lesson's main sequences rather than merely counting mentions. Do
not select standing when it is only a brief orientation or closing check at the
beginning or end, and ignore brief transitional positions. When two positions share
the main work, include both; use at most three. Include only positions and supports
substantially used.
`notable_feature` is a genuinely distinctive factual characteristic, or null.
`suggested_title` is null unless the source title is missing, generic, or numbered;
it is conservative display copy, never canonical identity.

Also identify the presenter's intention: what the teacher frames this particular
lesson as investigating or helping with, not every benefit its movements could
theoretically provide. Return every application below exactly once:

- pain_comfort_recovery: Pain, comfort, and recovery
- everyday_movement: Everyday movement
- music_voice_performance: Music, voice, and performance
- sport_skilled_activity: Sport and skilled activity
- stress_rest_self_regulation: Stress, rest, and self-regulation
- learning_across_life: Learning across life
- creativity_meditation_self_knowledge: Creativity, meditation, and self-knowledge

Use `intended` only when the presenter explicitly frames the application as a
principal purpose, audience, or intended use of this lesson. Use `mentioned` for
an explicit but peripheral mention, and `absent` otherwise. Do not infer an
application from movements that might be useful for it or from generic
Feldenkrais conventions.

Mandatory exclusions:

- Breathing or airway ease is not music/voice/performance unless voice, music,
  dance, acting, or performance is framed as an application.
- Agility, balance, coordination, or strength is not sport/skilled activity
  unless a sport, athletic practice, or named skilled activity is framed as an
  application.
- Instructions to avoid pain, stay comfortable, or recover between movements
  are not pain/comfort/recovery unless pain, discomfort, injury, or recovery is
  framed as a target of the lesson.
- General Feldenkrais learning, awareness, or self-exploration framing —
  including “learning about yourself”, “exploring yourself”, and similar
  preamble — is not `learning_across_life` or
  `creativity_meditation_self_knowledge` unless the presenter frames it as this
  particular lesson’s principal purpose.

Evidence must be short verbatim transcript quotations. Public use is
`intended`-only, so precision matters more than recall.

## Source title

{source_title}

## Hosting-page context

{hosting_page_context}

## Transcript

{lesson_text}
