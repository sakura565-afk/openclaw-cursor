"""Find the beat grid in a clip's soundtrack.

The music video edit has to cut on the music, so the onsets are measured
rather than guessed: the audio is decoded to mono PCM, split into frames,
and the spectral flux (positive change of the magnitude spectrum) is
computed per frame. Peaks above a moving average mark onsets.
"""
from __future__ import annotations

import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

VIDEO = Path(sys.argv[1])
WORK = Path(sys.argv[2])
WORK.mkdir(parents=True, exist_ok=True)
WAV = WORK / "audio.wav"

subprocess.run(
    ["ffmpeg", "-y", "-v", "error", "-i", str(VIDEO), "-vn",
     "-ac", "1", "-ar", "22050", "-f", "wav", str(WAV)],
    check=True, timeout=300,
)

with wave.open(str(WAV), "rb") as handle:
    rate = handle.getframerate()
    raw = handle.readframes(handle.getnframes())
samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

hop = 512
window = 1024
frames = 1 + (len(samples) - window) // hop
magnitude = np.zeros(frames, dtype=np.float32)
for i in range(frames):
    chunk = samples[i * hop:i * hop + window]
    if len(chunk) < window:
        break
    spectrum = np.abs(np.fft.rfft(chunk * np.hanning(window)))
    magnitude[i] = np.sqrt((spectrum ** 2).mean())

flux = np.diff(magnitude, prepend=magnitude[0])
flux = np.maximum(flux, 0)

times = np.arange(frames) * hop / rate
# adaptive threshold: a peak has to stand out from its neighbourhood
smooth = np.convolve(flux, np.ones(9) / 9.0, mode="same")
peaks = []
min_gap = 0.18
last = -10.0
for i in range(2, len(flux) - 2):
    value = flux[i]
    if value <= flux[i - 1] or value < flux[i + 1]:
        continue
    if value < smooth[i] * 1.45 or value < 0.010:
        continue
    if times[i] - last < min_gap:
        if peaks and value > flux[int(peaks[-1] * rate / hop)]:
            peaks[-1] = times[i]
            last = times[i]
        continue
    peaks.append(times[i])
    last = times[i]

print(f"  длительность: {len(samples)/rate:.2f} с")
print(f"  найдено онсетов: {len(peaks)}")
print("  первые 24:", ", ".join(f"{p:.2f}" for p in peaks[:24]))
if len(peaks) > 2:
    gaps = np.diff(peaks)
    print(f"  интервалы: медиана {np.median(gaps):.3f} с  мин {gaps.min():.3f}  макс {gaps.max():.3f}")
    print(f"  темп (по медиане): {60/np.median(gaps):.1f} BPM")

# loudness contour in 0.5 s windows, to find where the track builds
print("  громкость по 0.5 с:")
for start in np.arange(0, len(samples) / rate, 0.5):
    a = int(start * rate)
    b = min(len(samples), a + int(0.5 * rate))
    if b <= a:
        break
    rms = float(np.sqrt((samples[a:b] ** 2).mean()))
    bar = "#" * int(rms * 90)
    print(f"    {start:5.1f} с  rms={rms:.4f} {bar}")
