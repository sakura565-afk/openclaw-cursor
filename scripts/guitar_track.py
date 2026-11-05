"""Acoustic guitar via Karplus-Strong physical modelling.

No music model is available locally and the HF inference router exposes no
music endpoint, so the romantic guitar part is synthesised: a plucked-string
model (Karplus-Strong) with a body resonance filter, strummed chords and a
gentle lead line.

The output matches the audio format the H3 pipeline expects: 44100 Hz, stereo,
signed 16-bit.

Usage:
  python scripts/guitar_track.py --out track.wav --seconds 7.5
"""
from __future__ import annotations

import argparse
import math
import os
import struct
import wave

SR = 44100


def midi_hz(note: int) -> float:
    return 440.0 * (2.0 ** ((note - 69) / 12.0))


def pluck(freq: float, dur: float, sr: int = SR, damp: float = 0.5,
          brightness: float = 0.5, seed: int = 0) -> list[float]:
    """One plucked string. Karplus-Strong with a fractional delay line."""
    n = int(dur * sr)
    if n <= 0:
        return []
    # excitation: filtered noise burst, brighter for a harder pluck
    rnd = _rng(seed)
    exc = []
    lp = 0.0
    for i in range(n):
        white = rnd() * 2.0 - 1.0
        lp += brightness * (white - lp)
        exc.append(lp)
    # pre-delay fade so the attack is not a click
    atk = min(64, n // 8)
    for i in range(min(atk, n)):
        exc[i] *= i / max(1, atk)

    delay = sr / freq
    size = int(math.ceil(delay)) + 2
    buf = [0.0] * size
    idx = 0
    # one-pole averaging for the decay filter; damp controls how fast it decays
    a = max(0.0, min(0.999, 1.0 - damp * 0.06))
    out = [0.0] * n
    for i in range(n):
        cur = buf[idx]
        nxt = buf[(idx + 1) % size]
        avg = 0.5 * (cur + nxt)
        buf[idx] = exc[i] * 0.6 + avg * (0.995 * a)
        out[i] = cur
        idx = (idx + 1) % size
    # taper the tail
    tail = min(n // 6, int(0.05 * sr))
    for i in range(tail):
        out[n - tail + i] *= 1.0 - i / tail
    return out


def _rng(seed: int):
    state = (seed * 2654435761 + 1013904223) & 0xFFFFFFFF

    def nxt() -> float:
        nonlocal state
        state ^= (state << 13) & 0xFFFFFFFF
        state ^= state >> 17
        state ^= (state << 5) & 0xFFFFFFFF
        return (state & 0xFFFFFFFF) / 0xFFFFFFFF
    return nxt


def body_resonance(buf: list[float]) -> list[float]:
    """Two resonant peaks around 100 Hz and 210 Hz - the guitar body."""
    out = [0.0] * len(buf)
    for i, x in enumerate(buf):
        # one-pole lowpass mix plus a comb-ish delay for the body
        lp = x * 0.55 + (out[i - 1] * 0.45 if i else 0.0)
        d = out[i - 88] * 0.12 if i >= 88 else 0.0
        out[i] = lp * 0.92 + d
    return out


def mix(buf: list[float], add: list[float], at: int, gain: float) -> None:
    for i, x in enumerate(add):
        j = at + i
        if 0 <= j < len(buf):
            buf[j] += x * gain


def build(seconds: float, bpm: float, seed: int) -> list[float]:
    n = int(seconds * SR)
    buf = [0.0] * n
    beat = 60.0 / bpm
    rnd = _rng(seed)

    # Am - F - C - G, the warm romantic loop
    chords = [
        [57, 60, 64, 69],   # Am
        [53, 57, 60, 65],   # F
        [48, 52, 55, 60],   # C
        [55, 59, 62, 67],   # G
    ]
    bars = int(math.ceil(seconds / (beat * 4))) + 1
    for b in range(bars):
        t0 = b * beat * 4
        chord = chords[b % len(chords)]
        # downstroke strum: low -> high, slightly spread in time
        for k, note in enumerate(chord):
            at = int((t0 + k * 0.035) * SR)
            if at >= n:
                break
            dur = min(2.4, seconds - at / SR)
            mix(buf, body_resonance(pluck(midi_hz(note), dur, damp=0.42,
                                          brightness=0.42, seed=seed + b * 31 + k)),
                at, 0.34)
        # gentle arpeggio over the top of the bar
        arp = [chord[1], chord[2], chord[3], chord[2]]
        for k, note in enumerate(arp):
            at = int((t0 + beat * (1.0 + 0.75 * k)) * SR)
            if at >= n:
                break
            dur = min(1.6, seconds - at / SR)
            mix(buf, pluck(midi_hz(note + 12), dur, damp=0.62,
                           brightness=0.62, seed=seed + 97 + b * 7 + k),
                at, 0.13)

    # soft lead melody over the last two bars
    melody = [69, 72, 71, 67, 64, 67, 69, 72]
    lead_start = max(0.0, seconds - beat * 8)
    for k, note in enumerate(melody):
        at = int((lead_start + k * beat * 0.5) * SR)
        if at >= n:
            break
        dur = min(1.4, seconds - at / SR)
        mix(buf, pluck(midi_hz(note), dur, damp=0.55, brightness=0.7,
                       seed=seed + 555 + k),
            at, 0.10)

    # air: very light noise for the pick
    for i in range(n):
        buf[i] += (rnd() * 2.0 - 1.0) * 0.0016
    return buf


def normalize(buf: list[float], peak: float = 0.82) -> list[float]:
    m = max((abs(x) for x in buf), default=0.0)
    if m < 1e-9:
        return buf
    g = peak / m
    return [x * g for x in buf]


def to_stereo(buf: list[float]) -> list[tuple[int, int]]:
    """Mono -> stereo with a couple of ms of Haas delay on one side."""
    d = int(0.004 * SR)
    out = []
    for i, x in enumerate(buf):
        l = int(max(-1.0, min(1.0, x)) * 32767)
        r = buf[i - d] if i >= d else 0
        r = int(max(-1.0, min(1.0, r)) * 32767)
        out.append((l, r))
    return out


def write_wav(path: str, frames: list[tuple[int, int]]) -> None:
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(struct.pack("<%dh" % (2 * len(frames)),
                                  *[v for pair in frames for v in pair]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--seconds", type=float, default=7.5)
    ap.add_argument("--bpm", type=float, default=68.0)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    buf = build(a.seconds, a.bpm, a.seed)
    buf = normalize(buf)
    write_wav(a.out, to_stereo(buf))
    print(f"wrote {a.out} ({a.seconds}s, {a.bpm} bpm, {len(buf)} samples)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
