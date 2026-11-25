"""Best moments of a stream recording (method adapted from OpenShorts clip_selection, MIT): transcript windows +
loudness peaks -> Gemini scores each candidate on hook, emotion and payoff -> non-overlapping clips snapped to words.

    python -m src.live_moments work/live/rec_<id>.mp4 --streamer "TGG" --clips 2 [--min 18 --max 45]
Writes work/live/<stem>_moments.json  [{start, end, score, title, hook, why}] and the transcript (<stem>_words.json).
"""
import argparse, json, re, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.llm_director import ask_json  # noqa: E402


def from_pipeline(tpath):
    """Words from the pipeline's source_transcript.json ({segments: [{words: [{word, start, end}]}]})."""
    tr = json.loads(Path(tpath).read_text(encoding="utf-8"))
    return [{"w": w["word"].strip(), "s": round(w["start"], 2), "e": round(w["end"], 2)} for s in tr["segments"] for w in s.get("words", [])]


def transcribe(media, out):
    if out.exists():
        return json.loads(out.read_text(encoding="utf-8"))
    from faster_whisper import WhisperModel
    try:
        m = WhisperModel("small.en", device="cuda", compute_type="float16")
        segs, _ = m.transcribe(str(media), word_timestamps=True, vad_filter=True)
        segs = list(segs)
    except Exception:  # CUDA libs missing on this machine: CPU int8
        m = WhisperModel("small.en", device="cpu", compute_type="int8", cpu_threads=8)
        segs, _ = m.transcribe(str(media), word_timestamps=True, vad_filter=True)
        segs = list(segs)
    words = [{"w": w.word.strip(), "s": round(w.start, 2), "e": round(w.end, 2)} for s in segs for w in (s.words or [])]
    out.write_text(json.dumps(words), encoding="utf-8")
    return words


def loudness(media):
    """Short-term loudness every 0.5 s (LUFS) -> {second: lufs}."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(media), "-af", "ebur128=framelog=info", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="ignore").stderr
    out = {}
    for t, s in re.findall(r"t:\s*([\d.]+)\s+TARGET.*?S:\s*(-?[\d.]+)", r):
        out[int(float(t))] = max(out.get(int(float(t)), -99.0), float(s))
    return out


def windows(words, size=40, step=15):
    if not words:
        return []
    end = words[-1]["e"]
    out, t = [], 0.0
    while t < end:
        ws = [w for w in words if t <= w["s"] < t + size]
        if ws:
            out.append({"start": round(t, 1), "end": round(min(t + size, end), 1),
                        "text": " ".join(f"[{w['s']:.0f}]{w['w']}" if i % 12 == 0 else w["w"] for i, w in enumerate(ws))})
        t += step
    return out


def snap(start, end, words):
    """Start on a word boundary, end after the last full sentence-ish word inside the range."""
    inside = [w for w in words if w["s"] >= start - 0.3 and w["e"] <= end + 0.6]
    if not inside:
        return start, end
    s = inside[0]["s"] - 0.15
    stops = [w["e"] for w in inside if re.search(r"[.!?]$", w["w"])]
    e = (stops[-1] if stops and stops[-1] - s >= 12 else inside[-1]["e"]) + 0.25
    return round(max(0, s), 2), round(e, 2)


P = """You pick the most viral moments of a live GTA 6 launch stream by {streamer} for YouTube Shorts / TikTok.
Each candidate window has the transcript (numbers in [] are seconds) and the loudest moment (LUFS, higher = louder; reactions, screams, laughs).
Pick the {n} best NON-overlapping moments, each {mn}-{mx} s long, that work without context: a strong reaction, a funny line,
a surprise in the game, a wow at the graphics, a fail. The clip must START right before the setup (no dead air) and END on the payoff.
Avoid: sponsor reads, giveaway talk, chat reading, silence, slurs or anything graphic.
Return JSON list: [{{"start": s, "end": s, "score": 0-100, "title": "English Shorts title <= 50 chars with 1 emoji",
"hook": "on-screen hook text, max 6 words, ALL CAPS", "why": "1 line"}}]
CANDIDATES:
{cands}"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("media")
    ap.add_argument("--streamer", required=True)
    ap.add_argument("--clips", type=int, default=2)
    ap.add_argument("--min", type=float, default=18)
    ap.add_argument("--max", type=float, default=45)
    ap.add_argument("--transcript", help="pipeline source_transcript.json (skips a second transcription)")
    a = ap.parse_args()
    media = Path(a.media)
    words = from_pipeline(a.transcript) if a.transcript else transcribe(media, media.with_name(media.stem + "_words.json"))
    loud = loudness(media)
    cands = []
    for w in windows(words):
        peak_t = max(range(int(w["start"]), int(w["end"]) + 1), key=lambda s: loud.get(s, -99))
        cands.append({**w, "peak_s": peak_t, "peak_lufs": loud.get(peak_t, -99)})
    picks = ask_json(P.format(streamer=a.streamer, n=a.clips, mn=a.min, mx=a.max,
                              cands=json.dumps(cands, ensure_ascii=False)), temperature=0.3)
    picks = picks if isinstance(picks, list) else picks.get("moments", [])
    out = []
    for p in sorted(picks, key=lambda x: -x.get("score", 0)):
        s, e = snap(float(p["start"]), float(p["end"]), words)
        if e - s < a.min * 0.8 or any(not (e <= o["start"] or s >= o["end"]) for o in out):
            continue
        out.append({**p, "start": s, "end": min(e, s + a.max)})
    (media.with_name(media.stem + "_moments.json")).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    # segments file for `pipeline.py --clip-url ... --segments-file` (hook text = 2 lines on screen)
    segs = []
    for o in out:
        words_h = o.get("hook", "").split()
        mid = (len(words_h) + 1) // 2
        segs.append({"start": o["start"], "end": o["end"], "title": o.get("title", ""), "why": o.get("why", ""),
                     "hook_line1": " ".join(words_h[:mid]), "hook_line2": " ".join(words_h[mid:]), "scores": {"safety": 90}})
    (media.with_name(media.stem + "_segments.json")).write_text(json.dumps({"segments": segs}, ensure_ascii=False, indent=1), encoding="utf-8")
    for o in out:
        print(f"{o['score']:>3} {o['start']:.1f}-{o['end']:.1f} ({o['end'] - o['start']:.0f}s) {o['title']} | {o['hook']}")


if __name__ == "__main__":
    main()
