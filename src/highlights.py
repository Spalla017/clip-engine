"""Find the loudest, busiest moments of a gameplay recording (no speech needed).

For your own gameplay after launch: crashes, explosions, chases and shouting are loud, so audio energy is a
cheap first pass. It proposes windows; you still look at them before posting.

  python -m src.highlights VIDEO --clips 5 [--seconds 22]
"""
import argparse
import subprocess

import numpy as np

from .common import probe, say

SR = 8000


def envelope(video, hop=0.5):
    """RMS (dB) every `hop` seconds, decoded straight from ffmpeg (no temp file)."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"],
                         capture_output=True).stdout
    x = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768
    n = int(SR * hop)
    if len(x) < n * 4:
        return np.array([]), hop
    frames = x[: len(x) // n * n].reshape(-1, n)
    return 20 * np.log10(np.sqrt((frames ** 2).mean(1)) + 1e-9), hop


def find(video, clips=5, seconds=22.0, lead=0.35):
    """Top `clips` non-overlapping windows. Each starts `lead` of its length before the peak (build-up first)."""
    env, hop = envelope(video)
    total = probe(video)["duration"]
    if not len(env):
        return []
    # Loudness above the recording's own typical level, plus how suddenly it rose (an event, not a loud stretch).
    base = np.median(env)
    rise = np.maximum(0, env - np.concatenate([[env[0]] * 4, env[:-4]]))
    score = np.maximum(0, env - base) + 0.6 * rise
    win = max(2, int(3 / hop))
    smooth = np.convolve(score, np.ones(win) / win, mode="same")
    out, taken = [], np.zeros(len(env), dtype=bool)
    span = int(seconds / hop)
    for idx in np.argsort(-smooth):
        if len(out) >= clips or smooth[idx] <= 0:
            break
        start = int(max(0, min(idx - span * lead, len(env) - span)))
        if start < 0 or taken[start:start + span].any():
            continue
        taken[start:start + span] = True
        s = round(start * hop, 2)
        out.append({"start": s, "end": round(min(total, s + seconds), 2), "peak_at": round(idx * hop, 1),
                    "energy": round(float(smooth[idx]), 1)})
    return sorted(out, key=lambda o: o["start"])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--clips", type=int, default=5)
    ap.add_argument("--seconds", type=float, default=22.0)
    a = ap.parse_args()
    for i, h in enumerate(find(a.video, a.clips, a.seconds), 1):
        say(f"{i}. {h['start']:7.1f} - {h['end']:7.1f} s | pico em {h['peak_at']} s | energia {h['energy']}")


if __name__ == "__main__":
    main()
