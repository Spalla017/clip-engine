"""Free local SFX palette (whoosh, impacto, pop) synthesized with numpy.

Replaces the paid ElevenLabs call of maquina-cortes-gta's gerar-paleta-sfx.mjs: same three sounds,
same names, zero cost and no third-party audio (nothing for Content ID to match).
"""
import wave

import numpy as np

from .common import SFX

SR = 48000


def _save(name, x):
    x = np.asarray(x, dtype=np.float64)
    x = x / (np.max(np.abs(x)) + 1e-9) * 0.89
    path = SFX / f"{name}.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((x * 32767).astype("<i2").tobytes())
    return path


def _whoosh(dur=0.5, seed=1):
    n = int(SR * dur)
    t = np.linspace(0, 1, n)
    noise = np.random.default_rng(seed).standard_normal(n)
    # Band of noise that sweeps up then down: one-pole filters with moving cutoff.
    cutoff = 400 + 5200 * np.sin(np.pi * t) ** 2
    out = np.zeros(n)
    low = 0.0
    for i in range(n):
        a = 1 - np.exp(-2 * np.pi * cutoff[i] / SR)
        low += a * (noise[i] - low)
        out[i] = low
    out = np.diff(out, prepend=0.0) * 6 + out * 0.4
    return out * np.sin(np.pi * t) ** 1.6


def _impact(dur=0.5):
    n = int(SR * dur)
    t = np.arange(n) / SR
    freq = 46 + 110 * np.exp(-t * 26)
    body = np.sin(2 * np.pi * np.cumsum(freq) / SR) * np.exp(-t * 9)
    click = np.random.default_rng(2).standard_normal(n) * np.exp(-t * 180) * 0.5
    return np.tanh((body + click) * 1.8)


def _pop(dur=0.18):
    n = int(SR * dur)
    t = np.arange(n) / SR
    freq = 520 + 900 * np.exp(-t * 60)
    return np.sin(2 * np.pi * np.cumsum(freq) / SR) * np.exp(-t * 34)


def ensure_palette(force=False):
    """Create the three sounds once. Returns {name: path}."""
    makers = {"whoosh": _whoosh, "impacto": _impact, "pop": _pop}
    out = {}
    for name, fn in makers.items():
        path = SFX / f"{name}.wav"
        if force or not path.exists():
            _save(name, fn())
        out[name] = path
    return out


if __name__ == "__main__":
    print({k: str(v) for k, v in ensure_palette(force=True).items()})
