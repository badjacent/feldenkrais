# ElevenLabs TTS Integration — Sound Slipper

Research notes for mapping VoiceSpec parameters to ElevenLabs API capabilities.

## VoiceSpec → ElevenLabs Parameter Mapping

### Directly Supported

| VoiceSpec Field | ElevenLabs Parameter | Notes |
|---|---|---|
| `tempo_wpm` | `voice_settings.speed` (0.7–1.2) | Partial match. ElevenLabs speed is a multiplier, not WPM. Convert: `speed = tempo_wpm / 130.0` (assuming 130 WPM baseline), then clamp to [0.7, 1.2]. The range only covers ~91–156 WPM — our 68 WPM voices are below floor. |
| `speaker_identity` | `voice_id` | Map identity labels ("warm_neutral_female", "warm_low_male") to specific ElevenLabs voice IDs. See Voice Selection below. |
| `emphasis_words` | SSML `<emphasis>` tag (v2 models) or `[emphasized]` audio tag (v3) | Wrap emphasis words in tags before sending text. |
| `pause_ms_between_phrases` | SSML `<break time="Xs"/>` (v2) or `[pause]`/`[long pause]` (v3) | Insert break tags between phrases. v2 supports up to 3s breaks. v3 uses descriptive pause tags only (no exact ms control). |

### Partially Supported (via voice_settings sliders)

| VoiceSpec Field | ElevenLabs Approximation | Gap |
|---|---|---|
| `breathiness` (0–1) | `voice_settings.stability` (inverse relationship) | Lower stability introduces more variation and breathiness. Not a direct breathiness control — it also affects pitch wobble and emotional range. Workaround: for v3, prepend `[breathy]` or `[soft]` audio tags. |
| `intimacy` (0–1) | `voice_settings.stability` (low) + audio tags `[whispers]`, `[soft]` | No dedicated intimacy parameter. Combine low stability with whispering/soft audio tags and slow speed. High intimacy (>0.7) → `[whispers]` or `[softly]`. |
| `authority` (0–1) | `voice_settings.stability` (high) + `voice_settings.similarity_boost` (high) | High stability + high similarity = more authoritative, consistent delivery. Low authority → lower stability for more casual feel. |
| `brightness` (0–1) | Voice selection only | No runtime brightness control. Choose a brighter or darker voice at design time. Could use Voice Design prompt: "bright, clear timbre" vs "warm, dark timbre". |
| `smile_tone` (0–1) | v3 audio tags: `[cheerfully]`, `[playfully]`, `[smiling]` | No numeric smile parameter. Inject audio tags when smile_tone > threshold (e.g., >0.3). |
| `prosody_shape` | v3 audio tags + text structuring | No direct prosody contour control. See Prosody Mapping below. |

### Not Supported (Require Workarounds or Accept Loss)

| VoiceSpec Field | Status | Workaround |
|---|---|---|
| `pitch_hz_center` | Not controllable | ElevenLabs does not expose pitch in Hz. Choose a voice with appropriate pitch. SSML `<prosody pitch="+X%">` works on v2 models only. |
| `pitch_variability_semitones` | Not controllable | `stability` slider is the closest proxy (low stability = more pitch variation) but not calibrated in semitones. |
| `roughness` (0–1) | Not controllable at runtime | Choose a voice with inherent roughness. Voice Design prompt can request "gravelly" or "raspy". |
| `phrase_final_drop_semitones` | Not controllable | Natural to most voices but magnitude not adjustable. v3 audio tags `[deliberate]` or `[resigned tone]` may increase terminal drop. |

### Prosody Shape Mapping (v3 Audio Tags)

| ProsodyShape | Strategy |
|---|---|
| `FLAT` | `[flatly]` or `[deadpan]` audio tag |
| `FALLING` | `[resigned tone]` + `[slows down]` at end |
| `RISING` | `[curious]` or `[excited]` at phrase end |
| `WIDE_ARC` | `[dramatic]` — wide pitch excursion |
| `LOW_ARC` | `[calm]` + `[deliberate]` |
| `DESCENDING_RELEASE` | `[sighs]` or `[exhales]` at phrase tail, `[softly]` |
| `GENTLE_RISE_THEN_FALL` | `[warmly]` — natural conversational contour |

These are heuristic approximations. ElevenLabs v3 audio tags influence delivery but do not give deterministic prosodic contours.

## Voice Selection

### Pre-made Voices (Recommended Starting Point)

Search the ElevenLabs Voice Library for voices matching these profiles:

| speaker_identity | Target Profile | Search Criteria |
|---|---|---|
| `warm_neutral_female` | ~180 Hz fundamental, low breathiness, warm timbre | "Female, 30s-40s, calm, warm, American neutral accent, studio quality" |
| `warm_low_male` | ~105 Hz fundamental, intimate, low brightness | "Male, 40s-50s, deep, warm, American neutral accent, studio quality" |

Use `elevenlabs.voices.search()` to find candidates, then audition with lesson-typical phrases like "Only notice the first hint of widening."

### Voice Design (Custom Generation)

If no pre-made voice fits, use the Voice Design API with prompts:

```
warm_neutral_female:
"Native American English. Female, in her late 30s. Studio quality.
Persona: somatic guide, meditation teacher. Emotion: calm, warm, gentle.
Smooth timbre with subtle breathiness. Speaks at a slow, unhurried pace
like guiding someone through a body scan. Low-moderate pitch."

warm_low_male:
"Native American English. Male, in his late 40s. Studio quality.
Persona: mindful movement teacher. Emotion: calm, grounded, intimate.
Deep warm voice with resonant low tones. Speaks slowly and deliberately,
with natural pauses. Projects quiet authority without rigidity."
```

### Professional Voice Cloning (Highest Quality)

For production use, PVC with 1–3 hours of training audio from a real Feldenkrais teacher would produce the most natural results. This requires the Scale plan ($330/mo) or higher.

## Architecture: Pre-generate vs Stream on Demand

### Option A: Pre-generate All Clips at Lesson Load (Recommended)

```
Lesson loaded → extract all unique (text, VoiceSpec) pairs
  → batch API calls to /v1/text-to-speech/{voice_id}
  → cache audio files (WAV/MP3) keyed by hash(text + voice_settings)
  → LessonRunner plays from local cache
```

**Pros:**
- Zero latency during lesson playback — critical for breath-phase-locked cues
- Can retry failed generations before lesson starts
- Audio can be cached across sessions (same cue = same audio)
- Predictable cost (generate once, play many times)

**Cons:**
- Initial load delay (estimate: 2–5 seconds for a 10-minute lesson with ~20 cue phrases)
- Cache management needed

### Option B: Stream on Demand

```
Block fires → send text to streaming endpoint
  → play audio chunks as they arrive
```

**Pros:**
- No load delay
- Supports dynamic text (adapted prompts)

**Cons:**
- 75–135ms latency (Flash v2.5) or 200–400ms (v3) before first audio byte
- Incompatible with tight phase-locking (inhale_onset anchor with -120ms offset)
- Network jitter risks audio glitches during lesson

### Recommendation

**Use Option A (pre-generate) as the primary path.** The Sound Slipper's phase-locked architecture demands deterministic timing — network latency during playback would undermine the breath-synchronized cue delivery.

For the `adapt_block_from_response()` case where tempo_wpm changes mid-lesson: pre-generate a few speed variants (0.9x, 1.0x, 1.1x) at load time, or accept a brief pause to regenerate the clip.

### Model Selection

| Use Case | Model | Why |
|---|---|---|
| Production (pre-generated) | `eleven_multilingual_v2` | Highest quality, most stable, 10k char limit covers full lessons |
| Production with audio tags | `eleven_v3` | Best expressiveness, supports `[whispers]`/`[pause]` tags, 5k char limit |
| Development/testing | `eleven_flash_v2_5` | 50% cheaper, fast iteration, good enough for testing |

## Cost Estimation

### Per-Character Pricing (as of March 2026)

| Plan | Monthly Credits | Overage per 1k chars | Flash Discount |
|---|---|---|---|
| Creator ($11/mo) | 100k | $0.30 | 0.5 credits/char |
| Pro ($99/mo) | 500k | $0.24 | 0.5 credits/char |
| Scale ($330/mo) | 2M | $0.18 | 0.5 credits/char |

### 10-Minute Lesson Estimate

Assumptions:
- Demo lesson has 2 blocks, each repeating 5–6 times = ~11 spoken phrases
- A full 10-minute lesson might have 8–12 blocks, ~30–50 unique spoken phrases
- Average phrase length: ~60 characters (e.g., "Only notice the first hint of widening. Where does it begin?")
- Total characters per lesson: ~2,000–3,000

| Scenario | Characters | Cost (Pro plan overage) |
|---|---|---|
| Single lesson generation | 3,000 | $0.72 |
| 3 speed variants per phrase | 9,000 | $2.16 |
| 20 lessons, single variant | 60,000 | $14.40 |
| 20 lessons, 3 variants | 180,000 | $43.20 |

With the Pro plan's 500k included credits, ~165 full lessons fit within the monthly allowance (no overage). This is very affordable for development and small-scale use.

**Flash model halves these costs** — use `eleven_flash_v2_5` during development.

## Code Sketch: ElevenLabs TTS Provider

```python
"""ElevenLabs TTS provider implementing the Sound Slipper TTS Protocol."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

from elevenlabs.client import ElevenLabs
from elevenlabs.types import VoiceSettings

from sound_slipper import VoiceSpec, ProsodyShape, TTS


# Map speaker_identity labels to ElevenLabs voice IDs.
# Populate after auditioning voices from the library.
VOICE_ID_MAP: dict[str, str] = {
    "warm_neutral_female": "PLACEHOLDER_VOICE_ID",  # audition and fill
    "warm_low_male": "PLACEHOLDER_VOICE_ID",
}

# Prosody shape → v3 audio tag prefix
PROSODY_TAGS: dict[ProsodyShape, str] = {
    ProsodyShape.FLAT: "[flatly] ",
    ProsodyShape.FALLING: "[resigned tone] ",
    ProsodyShape.RISING: "[curious] ",
    ProsodyShape.WIDE_ARC: "[dramatic] ",
    ProsodyShape.LOW_ARC: "[calm] [deliberate] ",
    ProsodyShape.DESCENDING_RELEASE: "[softly] ",
    ProsodyShape.GENTLE_RISE_THEN_FALL: "[warmly] ",
}

BASELINE_WPM = 130.0


class ElevenLabsTTS:
    """TTS provider backed by ElevenLabs API.

    Satisfies the sound_slipper.TTS protocol:
        def speak(self, text: str, voice: VoiceSpec) -> None

    Designed for pre-generation workflow: call pregenerate_lesson()
    at load time, then speak() plays from cache with zero latency.
    """

    def __init__(
        self,
        api_key: str,
        cache_dir: Path = Path(".tts_cache"),
        model_id: str = "eleven_v3",
    ) -> None:
        self.client = ElevenLabs(api_key=api_key)
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.model_id = model_id
        self._audio_cache: dict[str, Path] = {}

    # ---- TTS Protocol ----

    def speak(self, text: str, voice: VoiceSpec) -> None:
        """Play pre-generated audio for the given text + voice."""
        cache_key = self._cache_key(text, voice)
        audio_path = self._audio_cache.get(cache_key)

        if audio_path and audio_path.exists():
            self._play_audio(audio_path)
        else:
            # Fallback: generate synchronously (adds latency)
            audio_path = self._generate_and_cache(text, voice)
            self._play_audio(audio_path)

    # ---- Pre-generation ----

    def pregenerate_lesson(self, lesson) -> None:
        """Pre-generate all voice clips for a lesson at load time."""
        for block in lesson.blocks:
            if block.voice:
                self._generate_and_cache(block.prompt.phrase, block.voice)
                if block.fallback_prompt:
                    self._generate_and_cache(
                        block.fallback_prompt.phrase, block.voice
                    )

    # ---- Internal ----

    def _voice_settings(self, voice: VoiceSpec) -> VoiceSettings:
        """Map VoiceSpec fields to ElevenLabs VoiceSettings."""
        # Speed: convert WPM to multiplier, clamp to API range
        speed = max(0.7, min(1.2, voice.tempo_wpm / BASELINE_WPM))

        # Stability: inverse of breathiness + intimacy blend
        # High breathiness/intimacy → low stability (more expressive)
        stability = max(0.0, min(1.0,
            1.0 - (0.5 * voice.breathiness + 0.3 * voice.intimacy)
        ))

        # Similarity boost: authority drives this up
        similarity_boost = max(0.0, min(1.0,
            0.5 + 0.4 * voice.authority
        ))

        # Style: derived from prosody shape complexity
        style = 0.3 if voice.prosody_shape in (
            ProsodyShape.WIDE_ARC, ProsodyShape.RISING
        ) else 0.0

        return VoiceSettings(
            stability=stability,
            similarity_boost=similarity_boost,
            style=style,
            speed=speed,
            use_speaker_boost=True,
        )

    def _prepare_text(self, text: str, voice: VoiceSpec) -> str:
        """Inject audio tags for prosody, emphasis, and intimacy."""
        parts: list[str] = []

        # Prosody shape tag
        tag = PROSODY_TAGS.get(voice.prosody_shape, "")
        if tag:
            parts.append(tag)

        # Intimacy → whispering
        if voice.intimacy > 0.7:
            parts.append("[softly] ")
        elif voice.intimacy > 0.5:
            parts.append("[gently] ")

        # Smile tone
        if voice.smile_tone > 0.3:
            parts.append("[warmly] ")

        # Breathiness
        if voice.breathiness > 0.4:
            parts.append("[breathy] ")

        parts.append(text)
        return "".join(parts)

    def _generate_and_cache(self, text: str, voice: VoiceSpec) -> Path:
        """Call ElevenLabs API, save result, return path."""
        cache_key = self._cache_key(text, voice)
        audio_path = self.cache_dir / f"{cache_key}.mp3"

        if audio_path.exists():
            self._audio_cache[cache_key] = audio_path
            return audio_path

        voice_id = VOICE_ID_MAP.get(
            voice.speaker_identity, list(VOICE_ID_MAP.values())[0]
        )
        prepared_text = self._prepare_text(text, voice)
        settings = self._voice_settings(voice)

        audio = self.client.text_to_speech.convert(
            text=prepared_text,
            voice_id=voice_id,
            model_id=self.model_id,
            voice_settings=settings,
            output_format="mp3_44100_128",
        )

        # audio is a generator of bytes
        with open(audio_path, "wb") as f:
            for chunk in audio:
                f.write(chunk)

        self._audio_cache[cache_key] = audio_path
        return audio_path

    def _cache_key(self, text: str, voice: VoiceSpec) -> str:
        """Deterministic hash of text + voice parameters."""
        blob = json.dumps({
            "text": text,
            "speaker": voice.speaker_identity,
            "tempo": voice.tempo_wpm,
            "breathiness": voice.breathiness,
            "intimacy": voice.intimacy,
            "authority": voice.authority,
            "smile_tone": voice.smile_tone,
            "prosody": voice.prosody_shape.value,
            "model": self.model_id,
        }, sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    def _play_audio(self, path: Path) -> None:
        """Play an audio file. Placeholder — real impl depends on platform."""
        # Option 1: elevenlabs built-in player
        # from elevenlabs.play import play
        # play(open(path, "rb").read())
        #
        # Option 2: platform audio (pygame, sounddevice, etc.)
        # Option 3: return bytes to LessonRunner for mixing with SoundSpec
        pass
```

## Open Questions

1. **Audio mixing**: The LessonRunner plays voice and sound simultaneously. The TTS provider returns an audio file, but it needs to be mixed with the SoundSpec output in real time. This likely requires a shared audio bus (e.g., `sounddevice` or Web Audio API) rather than simple file playback.

2. **v3 audio tag reliability**: Tags like `[breathy]` and `[warmly]` are "experimental" — they influence delivery but don't guarantee specific acoustic properties. Need to audition extensively.

3. **Cache invalidation**: When VoiceSpec parameters change via `adapt_block_from_response()`, the cache key changes and a new clip must be generated. Pre-generating speed variants (slow/normal) at load time mitigates this.

4. **Pause control**: `pause_ms_between_phrases` needs phrase-level splitting. The TTS provider should split text on sentence boundaries, insert `<break>` tags (v2) or `[pause]` (v3), and generate as a single clip — or generate per-phrase clips and let the LessonRunner handle inter-phrase silence.

5. **Pitch control gap**: `pitch_hz_center` and `pitch_variability_semitones` have no ElevenLabs equivalent. Voice selection is the only lever. This is the biggest fidelity gap in the mapping.

## Sources

- [ElevenLabs TTS API — Create Speech](https://elevenlabs.io/docs/api-reference/text-to-speech/convert)
- [Voice Settings](https://elevenlabs.io/docs/api-reference/voices/settings/get)
- [Models Overview](https://elevenlabs.io/docs/overview/models)
- [Voice Design](https://elevenlabs.io/docs/eleven-creative/voices/voice-design)
- [Voice Cloning](https://elevenlabs.io/docs/eleven-creative/voices/voice-cloning)
- [Best Practices (Prosody, SSML, Audio Tags)](https://elevenlabs.io/docs/overview/capabilities/text-to-speech/best-practices)
- [Streaming TTS](https://elevenlabs.io/docs/cookbooks/text-to-speech/streaming)
- [Python SDK](https://github.com/elevenlabs/elevenlabs-python)
- [API Pricing](https://elevenlabs.io/pricing/api)
- [ElevenLabs Pricing Breakdown (Flexprice)](https://flexprice.io/blog/elevenlabs-pricing-breakdown)
- [v3 Audio Tags](https://elevenlabs.io/blog/v3-audiotags)
- [Latency Optimization](https://elevenlabs.io/docs/developers/best-practices/latency-optimization)
