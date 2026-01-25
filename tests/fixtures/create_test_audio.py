"""
Create a minimal test audio file for integration tests.

Run this to generate test_audio.wav:
    python tests/fixtures/create_test_audio.py
"""

import struct
import wave


def create_test_wav(filename: str, duration_seconds: float = 1.0, sample_rate: int = 16000):
    """Create a simple WAV file with a sine wave tone."""
    import math

    num_samples = int(sample_rate * duration_seconds)
    frequency = 440  # A4 note

    # Generate sine wave samples
    samples = []
    for i in range(num_samples):
        t = i / sample_rate
        # Sine wave with fade in/out to avoid clicks
        fade = min(i / 1000, (num_samples - i) / 1000, 1.0)
        sample = int(32767 * fade * math.sin(2 * math.pi * frequency * t))
        samples.append(sample)

    # Write WAV file
    with wave.open(filename, "w") as wav:
        wav.setnchannels(1)  # Mono
        wav.setsampwidth(2)  # 16-bit
        wav.setframerate(sample_rate)
        wav.writeframes(struct.pack(f"<{len(samples)}h", *samples))

    print(f"Created {filename} ({duration_seconds}s, {sample_rate}Hz)")


if __name__ == "__main__":
    import os
    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_path = os.path.join(script_dir, "test_audio.wav")
    create_test_wav(output_path, duration_seconds=2.0)
