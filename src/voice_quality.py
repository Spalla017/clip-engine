"""Objective narration quality: noise floor, hiss, clipping and a speech-quality estimate.

Runs under VoiceStudio's Python (it has torch + torchaudio SQUIM); the pipeline calls it as a subprocess:

  <voicestudio>/.venv/Scripts/python.exe src/voice_quality.py FILE.wav [FILE2.wav ...] [--json OUT.json]

SQUIM (torchaudio.pipelines.SQUIM_OBJECTIVE) predicts PESQ / STOI / SI-SDR without a clean reference.
Thresholds for a clean voice (PASS) are in LIMITS; references/voz-limpa.md explains them.
"""
import argparse
import json
import sys

import numpy as np
import torch
import torchaudio

# "take": one generated sentence, before our gate (breaths can sit near -50 dB, so the floor is looser).
# "final": the whole narration after highpass + gate, i.e. what goes into the video.
# Calibration (2026-11-12): Edge-TTS 4.13 PESQ / 27.5 dB SDR; VoxCPM2 voice design 1.4-3.0 (the "robotic,
# static" takes the user rejected); VoxCPM2 cloned from a clean reference 3.6-4.0 / 22-28 dB.
PROFILES = {
    "take": {"noise_floor_db": -52.0, "hiss_db": -60.0, "pesq": 3.3, "stoi": 0.95, "si_sdr": 18.0, "peak_db": -0.5},
    "final": {"noise_floor_db": -62.0, "hiss_db": -62.0, "pesq": 3.3, "pesq_min": 2.8, "stoi": 0.95, "si_sdr": 18.0,
              "peak_db": -0.5},
}
LIMITS = PROFILES["final"]


def _load(path):
    import soundfile as sf  # torchaudio.load needs torchcodec, whose DLLs do not load on this Windows build
    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    return torch.from_numpy(data.mean(1)), sr


def measure(path, mode="final"):
    wav, sr = _load(path)
    x = wav.numpy().astype(np.float64)
    peak = 20 * np.log10(np.max(np.abs(x)) + 1e-12)
    hop = int(sr * 0.02)
    frames = x[: len(x) // hop * hop].reshape(-1, hop)
    rms = 20 * np.log10(np.sqrt((frames ** 2).mean(1)) + 1e-12)
    voiced = np.where(rms > rms.max() - 35)[0]
    span = frames[voiced[0]: voiced[-1] + 1] if len(voiced) else frames
    span_rms = 20 * np.log10(np.sqrt((span ** 2).mean(1)) + 1e-12)
    # Real pauses only: frames 28 dB below the median speech level. The 10th percentile used before also
    # caught syllable tails and failed clean takes.
    quiet = span[span_rms < np.median(span_rms) - 28]
    noise_floor = float(np.median(span_rms[span_rms < np.median(span_rms) - 28])) if len(quiet) >= 3 else -120.0
    hiss = -120.0
    if len(quiet) and sr >= 32000:
        spec = np.abs(np.fft.rfft(quiet * np.hanning(hop), axis=1)) ** 2
        freqs = np.fft.rfftfreq(hop, 1 / sr)
        band = spec[:, (freqs >= 7000) & (freqs <= 16000)].sum(1)
        hiss = float(10 * np.log10(band.mean() / (hop * hop / 4) + 1e-12))
    w16 = torchaudio.functional.resample(wav, sr, 16000)[None]
    model = _squim()
    with torch.no_grad():
        chunks = [w16[:, i:i + 16000 * 10] for i in range(0, w16.shape[1], 16000 * 10)]
        chunks = [c for c in chunks if c.shape[1] >= 16000 * 2] or [w16]
        stoi, pesq, sdr = zip(*[[float(v) for v in model(c)] for c in chunks])
    # Median over 10 s chunks. The minimum moves +-0.3 just by shifting where a chunk starts, so it only
    # guards against a really bad stretch (pesq_min).
    m = {"file": str(path), "peak_db": round(float(peak), 1), "noise_floor_db": round(noise_floor, 1),
         "hiss_db": round(hiss, 1), "pesq": round(float(np.median(pesq)), 2), "pesq_min": round(min(pesq), 2),
         "stoi": round(float(np.median(stoi)), 3), "si_sdr": round(float(np.median(sdr)), 1)}
    fails = []
    for k, lim in PROFILES[mode].items():
        bad = m[k] > lim if k in ("noise_floor_db", "hiss_db", "peak_db") else m[k] < lim
        if bad:
            fails.append(k)
    m["fails"] = fails
    m["status"] = "PASS" if not fails else "FAIL"
    m["score"] = round(m["pesq"] + m["si_sdr"] / 10 + min(0.0, (-60 - m["hiss_db"]) / 20), 3)
    return m


_MODEL = None


def _squim():
    global _MODEL
    if _MODEL is None:
        from torchaudio.pipelines import SQUIM_OBJECTIVE
        _MODEL = SQUIM_OBJECTIVE.get_model().eval()
    return _MODEL


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--json")
    ap.add_argument("--mode", default="final", choices=list(PROFILES))
    a = ap.parse_args()
    out = [measure(f, a.mode) for f in a.files]
    for m in out:
        name = m["file"].replace("\\", "/").split("/")[-1]
        print(f"{m['status']} {name:34} floor {m['noise_floor_db']:6.1f} hiss {m['hiss_db']:6.1f} "
              f"pesq {m['pesq']:.2f} stoi {m['stoi']:.3f} sdr {m['si_sdr']:5.1f} peak {m['peak_db']:5.1f}"
              + (f"  x {','.join(m['fails'])}" if m["fails"] else ""))
    if a.json:
        json.dump(out, open(a.json, "w", encoding="utf-8"), indent=2)
    sys.exit(0 if all(m["status"] == "PASS" for m in out) else 1)


if __name__ == "__main__":
    main()
