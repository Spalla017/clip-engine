"""Narration: Edge-TTS (default, fast) or VoiceStudio/VoxCPM2 (--voice voxcpm) -> pauses over 250 ms trimmed
-> word timestamps with faster-whisper."""
import asyncio
import re
from pathlib import Path

from .common import ffmpeg, probe, read_json, say, write_json

VOICES = {"andrew": "en-US-AndrewNeural", "brian": "en-US-BrianNeural", "ava": "en-US-AvaNeural",
          "antonio": "pt-BR-AntonioNeural", "francisca": "pt-BR-FranciscaNeural"}
# Keep 0.12 s of breath in every pause; highpass and light compression for phone speakers; 120 ms lead-in.
# Measured 2026-11-13: every filter lowers the speech-quality estimate of a clean voice (untreated median
# PESQ 3.81; gate 3.56; gate + compressor 3.59). So: no compressor, no gate, no time-stretch.
# Edge: only tighten long pauses to 0.12 s. Cloned voice: nothing (its pauses are already built per sentence).
# Both get a 120 ms lead-in and a short tail.
POST_EDGE = ("silenceremove=stop_periods=-1:stop_duration=0.25:stop_threshold=-45dB:stop_silence=0.12,"
             "adelay=120:all=1,apad=pad_dur=0.25")
# Gentle expander below -50 dBFS (pauses only): clean clone takes measured a -60 dB floor in the pauses, just
# over the -62 dB gate (2026-11-18); this brings them to ~-62 with PESQ 3.9 -> 3.8 and the speech untouched.
POST_CLONE = "agate=threshold=0.003:ratio=3:range=0.03:attack=5:release=150,adelay=120:all=1,apad=pad_dur=0.25"


def _edge(text, raw, voice, rate):
    import edge_tts

    async def _go():
        await edge_tts.Communicate(text, VOICES.get(voice, voice), rate=rate).save(str(raw))

    # The Edge service sometimes answers with no audio; it works on a second try.
    import time
    for attempt in range(4):
        try:
            asyncio.run(_go())
            # A truncated answer is a failure too: expect at least 1 s per 5 words.
            if Path(raw).exists() and probe(raw)["duration"] >= max(1.0, len(text.split()) / 5.0):
                return
        except Exception as e:
            if attempt == 3:
                raise RuntimeError(f"Edge-TTS sem áudio depois de 4 tentativas: {type(e).__name__}") from e
        time.sleep(3 + 4 * attempt)
    raise RuntimeError("Edge-TTS sem áudio depois de 4 tentativas")


def synthesize(text, out_wav, voice="auto", rate=None, engine="edge", lang="en", _try=1):
    """Narration that passed the clean-voice gate, 48 kHz mono. Returns (duration_seconds, engine_used).

    Default (--voice edge) is Edge-TTS, whole script in ~2 s: en-US-AndrewNeural +8 % for English, Antonio for pt.
    --voice clone (alias voxcpm) uses the channel's cloned VoxCPM2 profile of the language: slower (minutes),
    sentence by sentence with best-of-3 takes. Any other voice name is an Edge voice (andrew, brian, antonio...).
    """
    from . import voicestudio_client as vs
    from .common import LANGS

    out_wav = Path(out_wav)
    cfg = LANGS[lang]
    use_vs = engine == "voicestudio" or voice in ("clone", "voxcpm")
    used = "edge"
    if use_vs:
        raw = out_wav.with_suffix(".raw.wav")
        if not vs.start():
            raise RuntimeError("VoiceStudio não subiu (logs/voicestudio_backend.log). Use a voz padrão (Edge).")
        try:
            vs.narrate(text, raw, language=cfg["vs_lang"], log=say)
            used = "voicestudio"
        except RuntimeError as e:
            # Clean-voice law: a mispronounced cloned narration is not used. The user finds Edge robotic
            # (2026-11-16): retry the clone once, then stop; Edge only when asked with --voice edge.
            if _try < 2:
                say(f"  voz: voz clonada reprovada ({e}); gerando de novo com a clonada")
                return synthesize(text, out_wav, voice=voice, rate=rate, engine=engine, lang=lang, _try=_try + 1)
            raise RuntimeError(f"VOZ CLONADA REPROVADA 2x ({e}). Reescreva a frase apontada ou rode com --voice edge se o usuário aceitar.")
    else:
        raw = out_wav.with_suffix(".raw.mp3")
        name = cfg["edge_voice"] if voice in ("auto", "edge") else VOICES.get(voice, voice)
        _edge(text, raw, name, rate or cfg["edge_rate"])
    ffmpeg(["-i", raw, "-af", POST_CLONE if used == "voicestudio" else POST_EDGE, "-ar", "48000", "-ac", "1", out_wav], "tts")
    raw.unlink(missing_ok=True)
    # Clean-voice gate (references/voz-limpa.md): never hand a noisy or robotic narration to the render.
    if vs.installed():
        q = vs.quality([out_wav], "final")[0]
        say(f"  voz: {q['status']} pesq {q['pesq']} sdr {q['si_sdr']} piso {q['noise_floor_db']} chiado {q['hiss_db']}")
        if q["status"] != "PASS":
            if used == "voicestudio" and _try < 2:
                say("  voz: narração clonada reprovada no gate de voz limpa; gerando de novo com a clonada")
                return synthesize(text, out_wav, voice=voice, rate=rate, engine=engine, lang=lang, _try=_try + 1)
            raise RuntimeError(f"VOZ REPROVADA ({', '.join(q['fails'])}): {out_wav}. Veja references/voz-limpa.md")
    return probe(out_wav)["duration"], used


_MODEL = {}
_CUDA_BROKEN = False


def _cuda_dlls():
    """Make the pip-installed NVIDIA runtime (nvidia-cublas-cu12, nvidia-cudnn-cu12) visible on Windows."""
    import glob
    import os
    import site
    for base in site.getsitepackages() + [site.getusersitepackages()]:
        for d in glob.glob(os.path.join(base, "nvidia", "*", "bin")):
            try:
                os.add_dll_directory(d)
            except OSError:
                pass
            if d not in os.environ["PATH"]:
                os.environ["PATH"] = d + os.pathsep + os.environ["PATH"]


def release_gpu():
    """Drop Whisper from the GPU. The cloned voice needs ~5.7 GB of the 8 GB card while it generates;
    with Whisper loaded next to it the card overflows and generation gets about 10x slower."""
    import gc
    _MODEL.clear()
    gc.collect()


def _model(name, cuda):
    from faster_whisper import WhisperModel
    key = (name, cuda)
    if key not in _MODEL:
        _MODEL[key] = WhisperModel(name, device="cuda" if cuda else "cpu", compute_type="float16" if cuda else "int8")
    return _MODEL[key]


def _decode(audio, model, cuda, language, prompt):
    seg_iter, info = _model(model, cuda).transcribe(
        str(audio), beam_size=5, word_timestamps=True, language=language, initial_prompt=prompt,
        vad_filter=True, vad_parameters=dict(min_silence_duration_ms=500, speech_pad_ms=200))
    segments, captions = [], []
    for seg in seg_iter:
        words = []
        for w in seg.words or []:
            words.append({"word": w.word.strip(), "start": round(w.start, 3), "end": round(w.end, 3)})
            captions.append({"text": w.word, "startMs": int(w.start * 1000), "endMs": int(w.end * 1000)})
        segments.append({"start": round(seg.start, 3), "end": round(seg.end, 3), "text": seg.text.strip(),
                         "words": words})
    return segments, captions, info


def transcribe_file(media, out_json, language=None, prompt=None, model="large-v3-turbo"):
    """Word-level transcript (segments + Remotion captions). GPU first; CPU + small model as fallback."""
    global _CUDA_BROKEN
    # A transcript fixed by hand ("locked": true, e.g. a word Whisper missed under game noise) is never overwritten.
    prev = read_json(out_json, None) if Path(out_json).exists() else None
    if prev and prev.get("locked"):
        print(f"  transcrição travada (corrigida à mão): {Path(out_json).name}", flush=True)
        return prev
    import ctranslate2

    _cuda_dlls()
    cuda = ctranslate2.get_cuda_device_count() > 0 and not _CUDA_BROKEN
    media = Path(media)
    audio, tmp = media, None
    if media.suffix.lower() not in (".wav", ".mp3", ".m4a"):
        tmp = media.with_suffix(".asr.wav")
        ffmpeg(["-i", media, "-vn", "-ar", "16000", "-ac", "1", tmp], "asr")
        audio = tmp
    device = "cuda" if cuda else "cpu"
    try:
        segments, captions, info = _decode(audio, model if cuda else "small", cuda, language, prompt)
    except RuntimeError as e:
        if not cuda:
            raise
        # cuBLAS/cuDNN DLLs missing: pip install nvidia-cublas-cu12 "nvidia-cudnn-cu12>=9,<10"
        print(f"  whisper: GPU indisponível ({str(e)[:70]}), usando CPU/small", flush=True)
        _CUDA_BROKEN = True
        device = "cpu"
        segments, captions, info = _decode(audio, "small", False, language, prompt)
    if tmp:
        tmp.unlink(missing_ok=True)
    data = {"language": info.language, "duration": round(probe(media)["duration"], 2), "device": device,
            "word_count": len(captions), "segments": segments, "captions": captions}
    write_json(out_json, data)
    return data


def _key(word):
    return re.sub(r"[^a-z0-9à-ú]", "", word.lower())


def align_to_script(captions, script):
    """Show the script's spelling with Whisper's timing.

    Whisper hears 'gta seis' or 'reparem'; the script says 'GTA 6' and 'repara'. Words are matched in
    order; a misheard stretch with the same number of words on both sides takes the script's words.
    Stretches of different length keep what Whisper heard.
    """
    import difflib
    words = re.findall(r"\S+", script)
    heard = [c["text"].strip() for c in captions]
    out = [dict(c) for c in captions]
    matcher = difflib.SequenceMatcher(None, [_key(w) for w in heard], [_key(w) for w in words], autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        n, m = i2 - i1, j2 - j1
        if tag not in ("equal", "replace") or not n or not m:
            continue
        # Same count: word for word. Whisper merged or split words ("repara em" heard as "reparem"):
        # the last caption of the stretch takes the leftover script words, or absorbs the leftover time.
        for k in range(min(n, m)):
            last = k == min(n, m) - 1
            text = " ".join(words[j1 + k:j2]) if last and m > n else words[j1 + k]
            out[i1 + k]["text"] = (" " if captions[i1 + k]["text"].startswith(" ") else "") + text
            if last and n > m:
                out[i1 + k]["endMs"] = captions[i2 - 1]["endMs"]
                for extra in range(i1 + k + 1, i2):
                    out[extra]["text"] = ""
    # Whisper sometimes invents words in the silence after the narration ("Thank you."). Anything heard
    # after the last word that matches the script is not narration: drop it so it never becomes a caption.
    last = max((i2 for tag, i1, i2, j1, j2 in matcher.get_opcodes() if tag in ("equal", "replace")), default=len(out))
    return [c for c in out[:last] if c["text"].strip()]
