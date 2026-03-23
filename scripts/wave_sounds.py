#!/usr/bin/env python3
"""Generate wave-of-movement sounds for body regions.

Produces .wav files exploring different base sounds and wave-like
transformations. All synthesis — no external audio files needed.

Requirements:
    uv pip install numpy scipy

Usage:
    .venv/bin/python scripts/wave_sounds.py [output_dir]
    # Default output: ~/Desktop/wave_sounds/

Each base sound gets an original, octave-up, and octave-down variant.
Base sounds: drum hit, breath, surface slide, cello tone.
Transformations: time stretch, spectral sweep, granular scatter,
                 doppler, reverb, layering.

To add a new body region or transformation, add a make_* function
and call it from __main__. The save() helper normalizes and writes WAV.
"""

import sys
import numpy as np
from pathlib import Path
from scipy.io import wavfile
from scipy.signal import butter, sosfilt, sosfiltfilt

SAMPLE_RATE = 44100
OUTPUT_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / "Desktop" / "wave_sounds"


def save(name: str, audio: np.ndarray):
    """Save audio as 16-bit WAV."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # Normalize to 0.9 peak
    peak = np.max(np.abs(audio))
    if peak > 0:
        audio = audio * 0.9 / peak
    audio_16 = (audio * 32767).astype(np.int16)
    path = OUTPUT_DIR / f"{name}.wav"
    wavfile.write(str(path), SAMPLE_RATE, audio_16)
    print(f"  {path.name} ({len(audio) / SAMPLE_RATE:.1f}s)")


def envelope(duration: float, attack: float = 0.1, release: float = 0.3) -> np.ndarray:
    """Amplitude envelope with smooth attack and release."""
    n = int(duration * SAMPLE_RATE)
    env = np.ones(n)
    attack_n = int(attack * SAMPLE_RATE)
    release_n = int(release * SAMPLE_RATE)
    if attack_n > 0:
        env[:attack_n] = np.linspace(0, 1, attack_n) ** 2
    if release_n > 0:
        env[-release_n:] = np.linspace(1, 0, release_n) ** 2
    return env


def bandpass(audio: np.ndarray, low: float, high: float, order: int = 4) -> np.ndarray:
    """Apply bandpass filter."""
    nyq = SAMPLE_RATE / 2
    low_n = max(low / nyq, 0.001)
    high_n = min(high / nyq, 0.999)
    sos = butter(order, [low_n, high_n], btype='band', output='sos')
    return sosfiltfilt(sos, audio)


def lowpass(audio: np.ndarray, cutoff: float, order: int = 4) -> np.ndarray:
    nyq = SAMPLE_RATE / 2
    sos = butter(order, min(cutoff / nyq, 0.999), btype='low', output='sos')
    return sosfiltfilt(sos, audio)


# ── Base sound generators ──

def gen_drum_hit(duration: float = 0.3) -> np.ndarray:
    """Low drum-like hit — pitch drops rapidly from impact."""
    n = int(duration * SAMPLE_RATE)
    t = np.linspace(0, duration, n)
    # Frequency drops from 200Hz to 60Hz exponentially
    freq = 60 + 140 * np.exp(-t * 15)
    phase = 2 * np.pi * np.cumsum(freq) / SAMPLE_RATE
    tone = np.sin(phase)
    # Sharp attack, medium decay
    env = np.exp(-t * 8)
    # Add some noise for the attack
    noise = np.random.randn(n) * np.exp(-t * 30) * 0.3
    return (tone * env + noise)


def gen_breath(duration: float = 2.0) -> np.ndarray:
    """Filtered noise resembling a slow exhale."""
    n = int(duration * SAMPLE_RATE)
    noise = np.random.randn(n)
    # Bandpass that shifts down over time (exhale gets lower)
    result = np.zeros(n)
    chunk_size = SAMPLE_RATE // 10
    for i in range(0, n, chunk_size):
        end = min(i + chunk_size, n)
        progress = i / n
        center = 800 - 400 * progress  # 800Hz → 400Hz
        width = 200
        chunk = bandpass(noise[i:end], center - width, center + width)
        result[i:end] = chunk
    return result * envelope(duration, attack=0.3, release=0.5)


def gen_slide(duration: float = 1.5) -> np.ndarray:
    """Hand sliding on surface — filtered noise with texture."""
    n = int(duration * SAMPLE_RATE)
    noise = np.random.randn(n)
    # Modulate with slow random texture
    texture = lowpass(np.random.randn(n), 20) * 0.5 + 0.5
    shaped = noise * texture
    return bandpass(shaped, 200, 3000) * envelope(duration, 0.2, 0.4)


def gen_cello_tone(duration: float = 2.0, freq: float = 130.0) -> np.ndarray:
    """Low cello-like tone with harmonics and slight vibrato."""
    n = int(duration * SAMPLE_RATE)
    t = np.linspace(0, duration, n)
    # Vibrato
    vibrato = 1 + 0.005 * np.sin(2 * np.pi * 5.5 * t)
    f = freq * vibrato
    phase = 2 * np.pi * np.cumsum(f) / SAMPLE_RATE
    # Harmonics (sawtooth-ish, softened)
    tone = (np.sin(phase) * 1.0 +
            np.sin(2 * phase) * 0.5 +
            np.sin(3 * phase) * 0.25 +
            np.sin(4 * phase) * 0.12 +
            np.sin(5 * phase) * 0.06)
    # Bow noise
    noise = bandpass(np.random.randn(n), 1000, 4000) * 0.05
    return (tone + noise) * envelope(duration, 0.15, 0.4)


# ── Wave transformations ──

def time_stretch(audio: np.ndarray, factor: float) -> np.ndarray:
    """Simple time stretch via interpolation (no pitch preservation)."""
    n_out = int(len(audio) * factor)
    indices = np.linspace(0, len(audio) - 1, n_out)
    return np.interp(indices, np.arange(len(audio)), audio)


def spectral_sweep(audio: np.ndarray, low_start: float = 80, low_end: float = 400,
                   high_start: float = 200, high_end: float = 2000) -> np.ndarray:
    """Apply a bandpass filter that sweeps from low to high over time."""
    n = len(audio)
    result = np.zeros(n)
    n_chunks = 50
    chunk_size = n // n_chunks
    for i in range(n_chunks):
        start = i * chunk_size
        end = min(start + chunk_size, n)
        progress = i / n_chunks
        low = low_start + (low_end - low_start) * progress
        high = high_start + (high_end - high_start) * progress
        if end - start > 100:
            result[start:end] = bandpass(audio[start:end], low, high)
    return result


def add_reverb(audio: np.ndarray, decay: float = 0.4, delay_ms: float = 30,
               n_echoes: int = 8) -> np.ndarray:
    """Simple comb-filter reverb."""
    result = audio.copy()
    delay_samples = int(delay_ms * SAMPLE_RATE / 1000)
    for i in range(1, n_echoes + 1):
        offset = i * delay_samples
        gain = decay ** i
        padded = np.pad(audio, (offset, 0))[:len(audio)]
        result += padded * gain
    return result


def granular_scatter(audio: np.ndarray, grain_ms: float = 30,
                     scatter_ms: float = 100, n_grains: int = 200) -> np.ndarray:
    """Break audio into grains and scatter them, creating a traveling texture."""
    grain_n = int(grain_ms * SAMPLE_RATE / 1000)
    scatter_n = int(scatter_ms * SAMPLE_RATE / 1000)
    out_len = len(audio) + scatter_n * 2
    result = np.zeros(out_len)
    grain_env = np.hanning(grain_n)

    for _ in range(n_grains):
        # Pick a random position in the source
        src_pos = np.random.randint(0, max(1, len(audio) - grain_n))
        grain = audio[src_pos:src_pos + grain_n] * grain_env
        # Place it near its original position with some scatter
        out_pos = src_pos + np.random.randint(-scatter_n, scatter_n)
        out_pos = max(0, min(out_pos, out_len - grain_n))
        result[out_pos:out_pos + grain_n] += grain

    return result[:len(audio)]


def doppler_sweep(audio: np.ndarray, speed: float = 0.3) -> np.ndarray:
    """Simulate sound traveling past — pitch shifts and pans."""
    n = len(audio)
    t = np.linspace(-1, 1, n)
    # Pitch shift: compress/expand time based on "approach/recede"
    time_warp = np.cumsum(1 + speed * t) / SAMPLE_RATE
    time_warp = time_warp / time_warp[-1] * (n - 1)
    result = np.interp(np.arange(n), time_warp, audio)
    # Amplitude: louder as it passes
    amp = 1 / (1 + 2 * t ** 2)
    return result * amp


# ── Compositions ──

def make_leg_drum_wave():
    """Low drum hit → time stretched → spectral sweep → reverb."""
    hit = gen_drum_hit(0.3)
    stretched = time_stretch(hit, 10)  # 0.3s → 3s
    swept = spectral_sweep(stretched, 60, 300, 150, 800)
    return add_reverb(swept, decay=0.3, delay_ms=40, n_echoes=6)


def make_leg_breath_wave():
    """Breath → spectral sweep low to high → gentle reverb."""
    breath = gen_breath(3.0)
    swept = spectral_sweep(breath, 100, 500, 400, 2000)
    return add_reverb(swept, decay=0.25, delay_ms=35)


def make_leg_slide_granular():
    """Slide texture → granular scatter → reverb."""
    slide = gen_slide(2.0)
    scattered = granular_scatter(slide, grain_ms=40, scatter_ms=80, n_grains=300)
    return add_reverb(scattered, decay=0.3, delay_ms=25)


def make_leg_cello_sweep():
    """Low cello tone → spectral sweep → doppler."""
    tone = gen_cello_tone(3.0, freq=98)  # G2, low cello
    swept = spectral_sweep(tone, 80, 250, 200, 1000)
    return doppler_sweep(swept, speed=0.2)


def make_leg_drum_granular():
    """Drum hit → granular scatter → spectral sweep → reverb.
    The 'propagation through tissue' variation."""
    hit = gen_drum_hit(0.3)
    stretched = time_stretch(hit, 8)
    scattered = granular_scatter(stretched, grain_ms=25, scatter_ms=60, n_grains=250)
    swept = spectral_sweep(scattered, 50, 200, 120, 600)
    return add_reverb(swept, decay=0.35, delay_ms=30)


def make_leg_combined():
    """Layer: drum base + breath texture + cello undertone."""
    dur = 3.0
    drum = time_stretch(gen_drum_hit(0.3), 10)
    drum = spectral_sweep(drum, 50, 200, 120, 500) * 0.6

    breath = gen_breath(dur)
    breath = spectral_sweep(breath, 200, 600, 500, 1500) * 0.3

    cello = gen_cello_tone(dur, freq=82)  # E2
    cello = lowpass(cello, 400) * 0.4

    # Align lengths
    min_len = min(len(drum), len(breath), len(cello))
    combined = drum[:min_len] + breath[:min_len] + cello[:min_len]
    return add_reverb(combined, decay=0.3, delay_ms=35)


def pitch_shift(audio: np.ndarray, semitones: float) -> np.ndarray:
    """Pitch shift by resampling. Preserves duration.

    Positive semitones = higher pitch, negative = lower.
    Works by reading through the source at a different rate.
    """
    factor = 2 ** (semitones / 12)
    n = len(audio)
    # Read through source faster (up) or slower (down)
    src_indices = np.linspace(0, (n - 1) * factor, n) % (n - 1)
    return np.interp(src_indices, np.arange(n), audio)


def save_with_octaves(name: str, audio: np.ndarray):
    """Save original, octave up (+12 semitones), and octave down (-12)."""
    save(name, audio)
    save(name + "_octave_up", pitch_shift(audio, 12))
    save(name + "_octave_down", pitch_shift(audio, -12))


if __name__ == "__main__":
    print(f"Generating wave-of-movement sounds → {OUTPUT_DIR}/\n")

    print("1. Drum hit → stretch → sweep → reverb")
    save_with_octaves("01_leg_drum_wave", make_leg_drum_wave())

    print("2. Breath → sweep → reverb")
    save_with_octaves("02_leg_breath_wave", make_leg_breath_wave())

    print("3. Slide → granular → reverb")
    save_with_octaves("03_leg_slide_granular", make_leg_slide_granular())

    print("4. Cello → sweep → doppler")
    save_with_octaves("04_leg_cello_sweep", make_leg_cello_sweep())

    print("5. Drum → granular → sweep → reverb (tissue propagation)")
    save_with_octaves("05_leg_drum_granular", make_leg_drum_granular())

    print("6. Combined: drum + breath + cello layered")
    save_with_octaves("06_leg_combined", make_leg_combined())

    print(f"\nDone — {len(list(OUTPUT_DIR.glob('*.wav')))} files in {OUTPUT_DIR}")
