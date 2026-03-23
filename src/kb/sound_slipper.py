"""Sound Slipper — Declarative lesson engine for guided somatic exploration.

Phase-locked cue blocks with coordinated voice, sound, haptics, and prompts.
See build_demo_lesson() for example usage.

Integration points:
- Synth: backed by wave sound lab synthesis (tissue attenuation, resonant freq)
- TTS: ElevenLabs API for voice synthesis with fine-grained control
- Sensors: breath detection for phase-locking
- UI: web-based prompt display and response collection
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Protocol, Sequence, Any
import time


# ----------------------------
# Enums
# ----------------------------

class ProsodyShape(str, Enum):
    FLAT = "flat"
    FALLING = "falling"
    RISING = "rising"
    WIDE_ARC = "wide_arc"
    LOW_ARC = "low_arc"
    DESCENDING_RELEASE = "descending_release"
    GENTLE_RISE_THEN_FALL = "gentle_rise_then_fall"


class Waveform(str, Enum):
    SINE = "sine"
    FILTERED_NOISE = "filtered_noise"
    BANDPASSED_NOISE = "bandpassed_noise"
    DIFFUSE_NOISE = "diffuse_noise"
    SOFT_CLICK = "soft_click"
    LOW_TAP = "low_tap"
    BOWED_TONE = "bowed_tone"
    FILTERED_NOISE_PLUS_SINE = "filtered_noise_plus_sine"


class ModulationTarget(str, Enum):
    BREATH_IN = "breath_in"
    BREATH_OUT = "breath_out"
    BREATH_CYCLE = "breath_cycle"
    HEARTBEAT = "heartbeat"
    FREE = "free"


class Spatialization(str, Enum):
    CENTER = "center"
    CHEST_LIKE = "chest_like"
    THROAT_LIKE = "throat_like"
    BELOW_STERNUM = "below_sternum"
    BELOW_NAVEL = "below_navel"
    FACE = "face"
    DIFFUSE = "diffuse"
    FULL_BODY = "full_body"


class PerceptualDimension(str, Enum):
    EXPANSION = "expansion"
    PRESSURE = "pressure"
    SOFTENING = "softening"
    PULSE = "pulse"
    WARMTH = "warmth"
    CONTINUITY = "continuity"
    ASYMMETRY = "asymmetry"
    TEXTURE = "texture"
    DIRECTIONALITY = "directionality"
    EFFORT = "effort"
    CONTACT = "contact"


class ResolutionLevel(str, Enum):
    COARSE = "coarse"
    MEDIUM = "medium"
    FINE = "fine"


class ComparisonMode(str, Enum):
    FIRST_THING = "first_thing"
    WHICH_FIRST = "which_first"
    LEFT_RIGHT = "left_right"
    BEFORE_AFTER = "before_after"
    MORE_LESS = "more_less"
    CATEGORICAL = "categorical"
    EITHER_OR = "either_or"


class Anchor(str, Enum):
    INHALE_ONSET = "inhale_onset"
    MID_INHALE = "mid_inhale"
    EXHALE_ONSET = "exhale_onset"
    MID_EXHALE = "mid_exhale"
    POST_EXHALE_PAUSE = "post_exhale_pause"
    HEARTBEAT = "heartbeat"
    FIXED_TIME = "fixed_time"


class TargetRegion(str, Enum):
    LOWER_RIBS = "lower_ribs"
    UPPER_CHEST = "upper_chest"
    THROAT = "throat"
    JAW = "jaw"
    UPPER_ABDOMEN = "upper_abdomen"
    LOWER_ABDOMEN = "lower_abdomen"
    BACK_RIBS = "back_ribs"
    WHOLE_TRUNK = "whole_trunk"
    HEARTBEAT = "heartbeat"


class TargetEvent(str, Enum):
    INHALE_ONSET = "inhale_onset"
    MID_INHALE = "mid_inhale"
    EXHALE_ONSET = "exhale_onset"
    POST_EXHALE_PAUSE = "post_exhale_pause"
    HEARTBEAT = "heartbeat"
    JAW_RELEASE = "jaw_release"


# ----------------------------
# Core data models
# ----------------------------

@dataclass(slots=True)
class VoiceSpec:
    speaker_identity: str
    pitch_hz_center: float
    pitch_variability_semitones: float
    tempo_wpm: float
    pause_ms_between_phrases: int
    breathiness: float
    roughness: float
    brightness: float
    intimacy: float
    authority: float
    smile_tone: float
    prosody_shape: ProsodyShape
    phrase_final_drop_semitones: float
    emphasis_words: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SoundSpec:
    waveform: Waveform
    attack_ms: int
    decay_ms: int
    sustain_level: float
    release_ms: int
    amplitude_db: float
    pan: float
    spatialization: Spatialization
    haptic_enabled: bool

    fundamental_hz: Optional[float] = None
    partials: list[tuple[float, float]] = field(default_factory=list)
    bandwidth_hz: Optional[tuple[float, float]] = None
    vibrato_hz: float = 0.0
    vibrato_depth_cents: float = 0.0
    tremolo_hz: float = 0.0
    tremolo_depth: float = 0.0
    repetition_hz: Optional[float] = None
    modulation_target: Optional[ModulationTarget] = None
    haptic_freq_hz: Optional[float] = None
    haptic_amp: Optional[float] = None


@dataclass(slots=True)
class PromptSpec:
    phrase: str
    perceptual_dimension: PerceptualDimension
    resolution_level: ResolutionLevel
    comparison_mode: ComparisonMode
    allowed_answers: list[str] = field(default_factory=list)


@dataclass(slots=True)
class TimingSpec:
    anchor: Anchor
    offset_ms: int
    duration_ms: int
    peak_ms_relative_to_anchor: Optional[int] = None
    repeat_count: int = 1
    silence_after_ms: int = 0


@dataclass(slots=True)
class CueBlock:
    id: str
    name: str
    target_region: TargetRegion
    target_event: TargetEvent
    timing: TimingSpec
    voice: Optional[VoiceSpec]
    sound: Optional[SoundSpec]
    prompt: PromptSpec
    fallback_prompt: Optional[PromptSpec] = None
    notes: str = ""


@dataclass(slots=True)
class AudioBus:
    master_gain_db: float = -10.0
    voice_gain_db: float = -5.0
    sound_gain_db: float = -12.0
    haptic_gain: float = 0.18


@dataclass(slots=True)
class SensorsConfig:
    breath: bool = True
    heartbeat: bool = False
    touch: bool = False
    motion: bool = False


@dataclass(slots=True)
class Lesson:
    lesson_id: str
    title: str
    blocks: list[CueBlock]
    description: str = ""
    default_audio_bus: AudioBus = field(default_factory=AudioBus)
    sensors: SensorsConfig = field(default_factory=SensorsConfig)


# ----------------------------
# Runtime interfaces
# ----------------------------

class TTS(Protocol):
    def speak(self, text: str, voice: VoiceSpec) -> None: ...


class Synth(Protocol):
    def play(self, sound: SoundSpec, duration_ms: int) -> None: ...


class Haptics(Protocol):
    def play(self, frequency_hz: float, amplitude: float, duration_ms: int) -> None: ...


class UI(Protocol):
    def prompt_optional(self, prompt: PromptSpec) -> Optional[str]: ...


class Sensors(Protocol):
    def wait_for_anchor(self, anchor: Anchor, timeout_s: Optional[float] = None) -> Optional[float]:
        """Returns a monotonic timestamp when the anchor event occurred, or None."""
        ...


# ----------------------------
# Simple runtime state / adaptation
# ----------------------------

@dataclass(slots=True)
class UserState:
    last_answers: list[tuple[str, Optional[str]]] = field(default_factory=list)


def adapt_block_from_response(block: CueBlock, response: Optional[str]) -> None:
    if response is None:
        return

    if response == "unclear":
        block.timing.silence_after_ms += 400
        if block.voice:
            block.voice.tempo_wpm = max(50.0, block.voice.tempo_wpm - 4.0)

    elif response in {"irritating", "too_much"}:
        if block.sound:
            block.sound.amplitude_db -= 4.0
            if block.sound.bandwidth_hz:
                low, high = block.sound.bandwidth_hz
                center = (low + high) / 2.0
                width = max(100.0, (high - low) * 0.7 / 2.0)
                block.sound.bandwidth_hz = (center - width, center + width)


# ----------------------------
# Lesson engine
# ----------------------------

class LessonRunner:
    def __init__(
        self,
        sensors: Sensors,
        tts: TTS,
        synth: Synth,
        ui: UI,
        haptics: Optional[Haptics] = None,
    ) -> None:
        self.sensors = sensors
        self.tts = tts
        self.synth = synth
        self.ui = ui
        self.haptics = haptics

    def run_lesson(self, lesson: Lesson, user_state: Optional[UserState] = None) -> UserState:
        user_state = user_state or UserState()
        for block in lesson.blocks:
            self.run_block(block, user_state)
        return user_state

    def run_block(self, block: CueBlock, user_state: UserState) -> None:
        for _ in range(block.timing.repeat_count):
            event_time = self.sensors.wait_for_anchor(block.timing.anchor, timeout_s=10.0)

            if event_time is None:
                self._deliver_fallback(block, user_state)
                continue

            if block.timing.offset_ms > 0:
                time.sleep(block.timing.offset_ms / 1000.0)

            if block.sound:
                self.synth.play(block.sound, duration_ms=block.timing.duration_ms)

                if (block.sound.haptic_enabled and self.haptics
                        and block.sound.haptic_freq_hz and block.sound.haptic_amp):
                    self.haptics.play(
                        frequency_hz=block.sound.haptic_freq_hz,
                        amplitude=block.sound.haptic_amp,
                        duration_ms=block.timing.duration_ms,
                    )

            if block.voice:
                self.tts.speak(block.prompt.phrase, block.voice)

            time.sleep((block.timing.duration_ms + block.timing.silence_after_ms) / 1000.0)

            response = self.ui.prompt_optional(block.prompt)
            user_state.last_answers.append((block.id, response))
            adapt_block_from_response(block, response)

    def _deliver_fallback(self, block: CueBlock, user_state: UserState) -> None:
        if not block.fallback_prompt or not block.voice:
            return
        self.tts.speak(block.fallback_prompt.phrase, block.voice)
        response = self.ui.prompt_optional(block.fallback_prompt)
        user_state.last_answers.append((f"{block.id}:fallback", response))


# ----------------------------
# Spatialization → tissue path mapping
# ----------------------------

# Maps Spatialization enum to tissue paths for the sound lab's attenuation engine.
# Each path is a list of (tissue_type, distance_cm) tuples.
SPATIALIZATION_PATHS = {
    Spatialization.CENTER: [("muscle", 5)],
    Spatialization.CHEST_LIKE: [
        ("skin", 1), ("muscle", 3), ("bone", 1), ("muscle", 5), ("organ", 8),
    ],
    Spatialization.THROAT_LIKE: [
        ("skin", 0.5), ("cartilage", 2), ("muscle", 3),
    ],
    Spatialization.BELOW_STERNUM: [
        ("skin", 1), ("bone", 2), ("cartilage", 1), ("organ", 10),
    ],
    Spatialization.BELOW_NAVEL: [
        ("skin", 1), ("fat", 2), ("muscle", 4), ("organ", 12),
    ],
    Spatialization.FACE: [
        ("skin", 0.5), ("bone", 1), ("muscle", 2),
    ],
    Spatialization.DIFFUSE: [("muscle", 20)],
    Spatialization.FULL_BODY: [("muscle", 40)],
}

# Maps Spatialization to approximate body position (cm from feet, x offset)
SPATIALIZATION_BODY_POS = {
    Spatialization.CENTER: (120, 0),
    Spatialization.CHEST_LIKE: (130, 0),
    Spatialization.THROAT_LIKE: (158, 0),
    Spatialization.BELOW_STERNUM: (125, 0),
    Spatialization.BELOW_NAVEL: (100, 0),
    Spatialization.FACE: (168, 0),
    Spatialization.DIFFUSE: (120, 0),
    Spatialization.FULL_BODY: (100, 0),
}


# ----------------------------
# Lesson serialization (JSON)
# ----------------------------

def lesson_to_dict(lesson: Lesson) -> dict:
    """Serialize a Lesson to a JSON-compatible dict."""
    import dataclasses

    def _convert(obj):
        if isinstance(obj, Enum):
            return obj.value
        if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
            return {k: _convert(v) for k, v in dataclasses.asdict(obj).items()}
        if isinstance(obj, list):
            return [_convert(i) for i in obj]
        if isinstance(obj, tuple):
            return list(obj)
        if isinstance(obj, dict):
            return {k: _convert(v) for k, v in obj.items()}
        return obj

    return _convert(lesson)


def lesson_to_json(lesson: Lesson, indent: int = 2) -> str:
    """Serialize a Lesson to JSON string."""
    import json
    return json.dumps(lesson_to_dict(lesson), indent=indent)


# ----------------------------
# Example cue library
# ----------------------------

LOWER_RIBS_VOICE = VoiceSpec(
    speaker_identity="warm_neutral_female",
    pitch_hz_center=180.0,
    pitch_variability_semitones=1.2,
    tempo_wpm=78.0,
    pause_ms_between_phrases=900,
    breathiness=0.35,
    roughness=0.02,
    brightness=0.25,
    intimacy=0.70,
    authority=0.35,
    smile_tone=0.10,
    prosody_shape=ProsodyShape.DESCENDING_RELEASE,
    phrase_final_drop_semitones=2.0,
    emphasis_words=["first", "widen"],
)

LOWER_RIBS_SOUND = SoundSpec(
    waveform=Waveform.BANDPASSED_NOISE,
    fundamental_hz=None,
    partials=[],
    bandwidth_hz=(350.0, 900.0),
    attack_ms=180,
    decay_ms=120,
    sustain_level=0.35,
    release_ms=450,
    amplitude_db=-24.0,
    pan=0.0,
    vibrato_hz=0.0,
    vibrato_depth_cents=0.0,
    tremolo_hz=0.0,
    tremolo_depth=0.0,
    repetition_hz=None,
    modulation_target=ModulationTarget.BREATH_IN,
    spatialization=Spatialization.CHEST_LIKE,
    haptic_enabled=False,
)

LOWER_RIBS_BLOCK = CueBlock(
    id="b1",
    name="lower_rib_inhale_onset",
    target_region=TargetRegion.LOWER_RIBS,
    target_event=TargetEvent.INHALE_ONSET,
    timing=TimingSpec(
        anchor=Anchor.INHALE_ONSET,
        offset_ms=-120,
        duration_ms=1200,
        peak_ms_relative_to_anchor=300,
        repeat_count=5,
        silence_after_ms=2200,
    ),
    voice=LOWER_RIBS_VOICE,
    sound=LOWER_RIBS_SOUND,
    prompt=PromptSpec(
        phrase="Only notice the first hint of widening. Where does it begin?",
        perceptual_dimension=PerceptualDimension.EXPANSION,
        resolution_level=ResolutionLevel.FINE,
        comparison_mode=ComparisonMode.FIRST_THING,
        allowed_answers=["side_ribs", "front_ribs", "back_ribs", "unclear"],
    ),
    fallback_prompt=PromptSpec(
        phrase="If you do not feel widening, just notice whether the inhale feels more sideways or more upward.",
        perceptual_dimension=PerceptualDimension.DIRECTIONALITY,
        resolution_level=ResolutionLevel.COARSE,
        comparison_mode=ComparisonMode.EITHER_OR,
        allowed_answers=["sideways", "upward", "unclear"],
    ),
    notes="Target timing more than location.",
)

EXHALE_SETTLING_VOICE = VoiceSpec(
    speaker_identity="warm_low_male",
    pitch_hz_center=105.0,
    pitch_variability_semitones=0.8,
    tempo_wpm=68.0,
    pause_ms_between_phrases=1200,
    breathiness=0.22,
    roughness=0.01,
    brightness=0.18,
    intimacy=0.82,
    authority=0.20,
    smile_tone=0.05,
    prosody_shape=ProsodyShape.FALLING,
    phrase_final_drop_semitones=3.0,
    emphasis_words=["soften", "down"],
)

EXHALE_SETTLING_SOUND = SoundSpec(
    waveform=Waveform.SINE,
    fundamental_hz=196.0,
    partials=[],
    bandwidth_hz=None,
    attack_ms=40,
    decay_ms=100,
    sustain_level=0.22,
    release_ms=700,
    amplitude_db=-26.0,
    pan=0.0,
    vibrato_hz=0.0,
    vibrato_depth_cents=0.0,
    tremolo_hz=0.12,
    tremolo_depth=0.18,
    repetition_hz=None,
    modulation_target=ModulationTarget.BREATH_OUT,
    spatialization=Spatialization.BELOW_STERNUM,
    haptic_enabled=True,
    haptic_freq_hz=80.0,
    haptic_amp=0.18,
)

EXHALE_SETTLING_BLOCK = CueBlock(
    id="b2",
    name="upper_abdomen_exhale_settling",
    target_region=TargetRegion.UPPER_ABDOMEN,
    target_event=TargetEvent.EXHALE_ONSET,
    timing=TimingSpec(
        anchor=Anchor.EXHALE_ONSET,
        offset_ms=0,
        duration_ms=1800,
        peak_ms_relative_to_anchor=0,
        repeat_count=6,
        silence_after_ms=2500,
    ),
    voice=EXHALE_SETTLING_VOICE,
    sound=EXHALE_SETTLING_SOUND,
    prompt=PromptSpec(
        phrase="As the air leaves, what softens first: throat, breastbone, or upper belly?",
        perceptual_dimension=PerceptualDimension.SOFTENING,
        resolution_level=ResolutionLevel.MEDIUM,
        comparison_mode=ComparisonMode.WHICH_FIRST,
        allowed_answers=["throat", "breastbone", "upper_belly", "unclear"],
    ),
    notes="Encode release and descent, not anatomy doctrine.",
)


def build_demo_lesson() -> Lesson:
    return Lesson(
        lesson_id="lesson_10min_breath_frontline_01",
        title="Lower ribs and exhale settling",
        description="Minimal example lesson using phase-locked cues.",
        blocks=[LOWER_RIBS_BLOCK, EXHALE_SETTLING_BLOCK],
    )


# ----------------------------
# Stubs for testing
# ----------------------------

class DummySensors:
    def wait_for_anchor(self, anchor: Anchor, timeout_s: Optional[float] = None) -> Optional[float]:
        time.sleep(0.5)
        return time.monotonic()


class PrintTTS:
    def speak(self, text: str, voice: VoiceSpec) -> None:
        print(f"[TTS:{voice.speaker_identity}] {text}")


class PrintSynth:
    def play(self, sound: SoundSpec, duration_ms: int) -> None:
        print(
            f"[SOUND:{sound.waveform.value}] "
            f"dur={duration_ms}ms amp={sound.amplitude_db}dB "
            f"spatial={sound.spatialization.value}"
        )


class PrintHaptics:
    def play(self, frequency_hz: float, amplitude: float, duration_ms: int) -> None:
        print(f"[HAPTIC] freq={frequency_hz}Hz amp={amplitude} dur={duration_ms}ms")


class PrintUI:
    def prompt_optional(self, prompt: PromptSpec) -> Optional[str]:
        print(f"[PROMPT] {prompt.phrase}")
        print(f"  options={prompt.allowed_answers}")
        return None


if __name__ == "__main__":
    lesson = build_demo_lesson()

    # Print JSON representation
    print("=== Lesson JSON ===")
    print(lesson_to_json(lesson))
    print()

    # Run with stubs
    print("=== Running lesson ===")
    runner = LessonRunner(
        sensors=DummySensors(),
        tts=PrintTTS(),
        synth=PrintSynth(),
        haptics=PrintHaptics(),
        ui=PrintUI(),
    )
    runner.run_lesson(lesson)
