"""VoiceStudio (debpalash/voicestudio, AGPL-3.0) as the narration engine.

The backend runs headless from a source checkout (default C:\\Users\\<you>\\tools\\voicestudio, override with
VOICESTUDIO_HOME) on http://127.0.0.1:3900. Loopback needs no API key. This module only talks HTTP to it,
so none of its AGPL code is linked into the engine.

Model: VoxCPM2 (openbmb, Apache-2.0, Portuguese, commercial use allowed). Not OmniVoice: its weights are
CC-BY-NC (non-commercial) and a monetized channel is commercial use.

  python -m src.voicestudio_client status
  python -m src.voicestudio_client start
  python -m src.voicestudio_client candidates            # renders voice candidates to work/voices/
  python -m src.voicestudio_client make-profile FILE.wav --name "ClipEngine Narrador" --text "..."
  python -m src.voicestudio_client profiles
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from .common import DATA, LOGS, WORK, load_env, read_json, say, write_json

HOME = Path(os.environ.get("VOICESTUDIO_HOME", Path.home() / "tools" / "voicestudio"))
# Own instance on 3901 so it never collides with a VoiceStudio desktop app on 3900.
PORT = int(os.environ.get("CLIPENGINE_VOICESTUDIO_PORT", "3901"))
# Explicit folders: processes started from the Claude desktop app would otherwise write to its virtualized
# AppData (lost on reinstall) and download the models again from a normal terminal.
DATA_HOME = Path(os.environ.get("CLIPENGINE_VOICESTUDIO_DATA", Path.home() / "tools" / "voicestudio_data"))
BASE = f"http://127.0.0.1:{PORT}"
CONFIG = DATA / "voice.json"  # {"engine": "voicestudio", "voice": "<profile name>", "speed": 1.08, ...}

SAMPLE = ("Falta uma semana para o lançamento, e tem um detalhe no trailer que quase ninguém percebeu. "
          "Repara no fundo da cena: a cidade inteira está viva.")
# Reference line for a new voice: ~9 s, statement + question + numbers, no hard-to-read names.
REF_TEXT = ("Hoje eu vou te mostrar três detalhes que passaram despercebidos no trailer. "
            "O segundo é o mais estranho de todos, e você vai entender por quê.")
# Fast release: pauses between sentences are 170 ms; a slower gate never closes inside them.
GATE = "highpass=f=70,agate=threshold=0.003:ratio=8:attack=2:release=40:range=0.01"
VQ_SCRIPT = Path(__file__).resolve().parent / "voice_quality.py"

# VoxCPM2 voice design: a free-text description, no reference audio, no real person.
# Native American English candidates. The Portuguese-born voice keeps a non-American accent in English.
CANDIDATES_EN = {
    "us_m_young_energetic": "young adult male, energetic and upbeat, natural American English accent, gaming YouTuber narrator",
    "us_m_deep_warm": "adult male, deep warm voice, confident, natural American English accent, documentary narrator",
    "us_m_casual": "young adult male, relaxed conversational tone, natural American English accent, friendly storyteller",
}
SAMPLE_EN = ("GTA six is one week away, and there is a detail in the trailer almost nobody noticed. "
             "Look at the background of this shot: the whole city is alive.")
CANDIDATES = {
    "m_jovem_energico": "young adult male, energetic and upbeat, clear Brazilian Portuguese gaming narrator",
    "m_jovem_grave": "young adult male, deep warm voice, confident, Brazilian Portuguese storyteller",
    "m_adulto_locutor": "middle-aged male, rich low voice, radio announcer, Brazilian Portuguese",
    "f_jovem_energica": "young adult female, bright and energetic, Brazilian Portuguese gaming host",
}


def _req(method, path, body=None, headers=None, timeout=30):
    data = None
    hdrs = dict(headers or {})
    if isinstance(body, dict):
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    elif isinstance(body, bytes):
        data = body
    req = urllib.request.Request(BASE + path, data=data, headers=hdrs, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def health():
    try:
        status, body = _req("GET", "/health", timeout=5)
        return json.loads(body)
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read())
        except Exception:
            return {"status": f"http {e.code}"}
    except Exception:
        return None


def installed():
    venv = HOME / ".venv"
    return ((HOME / "backend" / "main.py").exists() and (venv / "Scripts" / "python.exe").exists()
            and (venv / "Lib" / "site-packages" / "uvicorn").exists() and (venv / "Lib" / "site-packages" / "torch").exists())


def start(wait=240):
    """Start the backend in the background if it is not up. Returns True when /health says ok."""
    h = health()
    if h and h.get("status") == "ok":
        return True
    if not h:
        if not installed():
            return False
        py = HOME / ".venv" / "Scripts" / "python.exe"
        log = open(LOGS / "voicestudio_backend.log", "a", encoding="utf-8")
        DATA_HOME.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "OMNIVOICE_PORT": str(PORT), "OMNIVOICE_BIND_HOST": "127.0.0.1", "PYTHONUTF8": "1",
               "OMNIVOICE_DATA_DIR": str(DATA_HOME), "OMNIVOICE_CACHE_DIR": str(DATA_HOME / "hf_cache"),
               "OMNIVOICE_TTS_BACKEND": config()["model"]}
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
        subprocess.Popen([str(py), "-m", "uvicorn", "main:app", "--app-dir", "backend", "--host", "127.0.0.1",
                          "--port", str(PORT)], cwd=str(HOME), env=env, stdout=log, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, creationflags=flags, close_fds=True)
    end = time.time() + wait
    while time.time() < end:
        h = health()
        if h and h.get("status") == "ok":
            return True
        time.sleep(2)
    return False


def config():
    load_env()
    cfg = read_json(CONFIG, {}) or {}
    return {"engine": cfg.get("engine", "voicestudio"), "voice": cfg.get("voice", ""),
            "model": cfg.get("model", "voxcpm2"), "speed": float(cfg.get("speed", 1.0)),
            "instruct": cfg.get("instruct", ""), "description": cfg.get("description", ""),
            "seed": cfg.get("seed", 42), "num_step": cfg.get("num_step", 16),
            "guidance_scale": cfg.get("guidance_scale")}


def speak(text, out_path, voice=None, language="pt-BR", speed=None, instruct=None, seed=None, description=None,
          num_step=None, guidance_scale=None, timeout=900):
    """POST /v1/audio/speech -> wav file. Raises RuntimeError with the server's message on failure."""
    cfg = config()
    body = {"model": cfg["model"], "input": text, "voice": voice or cfg["voice"] or "default",
            "language": language, "response_format": "wav", "speed": speed or cfg["speed"]}
    if instruct or (not (voice or cfg["voice"]) and cfg["instruct"]):
        body["instruct"] = instruct or cfg["instruct"]
    if description or (not (voice or cfg["voice"]) and cfg["description"]):
        body["description"] = description or cfg["description"]
    for k, v in (("num_step", num_step if num_step is not None else cfg.get("num_step")),
                 ("guidance_scale", guidance_scale if guidance_scale is not None else cfg.get("guidance_scale"))):
        if v is not None:
            body[k] = v
    if seed is not None or cfg["seed"] is not None:
        body["seed"] = seed if seed is not None else cfg["seed"]
    try:
        _, audio = _req("POST", "/v1/audio/speech", body, timeout=timeout)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"VoiceStudio {e.code}: {e.read()[:400].decode('utf-8', 'replace')}") from None
    Path(out_path).write_bytes(audio)
    return out_path


def profiles():
    _, body = _req("GET", "/profiles")
    data = json.loads(body)
    return data if isinstance(data, list) else data.get("profiles", data.get("items", []))


def make_profile(wav, name, ref_text, language="pt-BR"):
    """Clone profile from one of OUR synthetic candidates (never a real person's voice)."""
    boundary = uuid.uuid4().hex
    parts = []
    for k, v in (("name", name), ("ref_text", ref_text), ("language", language), ("kind", "clone"),
                 ("engine", config()["model"])):
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="ref_audio"; filename="{Path(wav).name}"\r\n'
                  "Content-Type: audio/wav\r\n\r\n").encode() + Path(wav).read_bytes() + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    try:
        _, body = _req("POST", "/profiles", b"".join(parts),
                       {"Content-Type": f"multipart/form-data; boundary={boundary}"}, timeout=300)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"VoiceStudio {e.code}: {e.read()[:400].decode('utf-8', 'replace')}") from None
    return json.loads(body)


def quality(files, mode="final"):
    """Measure wav files with src/voice_quality.py under VoiceStudio's Python (torch + SQUIM)."""
    import tempfile
    py = HOME / ".venv" / "Scripts" / "python.exe"
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "q.json"
        proc = subprocess.run([str(py), str(VQ_SCRIPT), *map(str, files), "--mode", mode, "--json", str(out)],
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        if not out.exists():
            raise RuntimeError("voice_quality falhou: " + (proc.stderr or proc.stdout)[-400:])
        return json.loads(out.read_text(encoding="utf-8"))


def _gate(src, dst):
    from .common import ffmpeg
    ffmpeg(["-i", src, "-af", GATE, "-ar", "48000", "-ac", "1", dst], "tts")
    return dst


def split_sentences(text, max_words=30, min_words=12):
    """Takes for the cloned voice: sentences, long ones split at a comma, very short ones joined to the next.

    Short takes ("Number three: Grassrivers.") are where the cloned voice glitches most, and long ones degrade.
    """
    import re
    parts = []
    for sent in re.split(r"(?<=[.!?…])\s+", text.strip()):
        words = sent.split()
        while len(words) > max_words:
            cut = max((i for i, w in enumerate(words[:max_words]) if w.endswith(",")), default=max_words - 1)
            parts.append(" ".join(words[:cut + 1]))
            words = words[cut + 1:]
        if words:
            parts.append(" ".join(words))
    out = []
    for part in parts:
        if out and len(out[-1].split()) < min_words and len(out[-1].split()) + len(part.split()) <= max_words + 6:
            out[-1] += " " + part
        else:
            out.append(part)
    if len(out) > 1 and len(out[-1].split()) < min_words:
        last = out.pop()   # pop first: `out[-2] += out.pop()` indexes after the pop and breaks on 2-item lists
        out[-1] += " " + last
    return out


def _tts_text(sentence):
    """Text sent to the model: no final full stop (the cloned voice sometimes reads it aloud as 'dot')."""
    return sentence.rstrip().rstrip(".").rstrip()


_NUM = set("""zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen
seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred thousand first second third
eighteenth nineteenth um uma dois duas tres três quatro cinco seis sete oito nove dez onze doze treze quatorze quinze
dezesseis dezessete dezoito dezenove vinte trinta quarenta cinquenta cem mil pm am""".split())


def _words(text):
    """Comparable words: lower-case, no punctuation, numbers dropped (Whisper writes '9', the script 'nine')."""
    import re
    out = []
    for w in re.sub(r"[^a-z0-9à-ú' ]", " ", text.lower()).split():
        w = w.strip("'")
        if w and not w[0].isdigit() and w not in _NUM:
            out.append(w)
    return out


VOCAB = ""  # game names for Whisper (set by pipeline.py from the profile's whisper_prompt)


def said_right(wav, text, language):
    """How well Whisper hears the sentence back: (score 0-1, [words spoken that are not in the text]).

    Catches what the noise metrics cannot: garbled or swallowed words, and extra words such as the
    cloned voice reading a full stop aloud ("Port Gellhorn. dot Rockstar..."). Made-up game names are
    compared by spelling similarity, so "Kalaga" heard as "Calaga" still counts.
    """
    import difflib
    from .voice_sync import transcribe_file
    tr = transcribe_file(wav, Path(wav).with_suffix(".asr.json"), language=language[:2], prompt=VOCAB or None)
    Path(wav).with_suffix(".asr.json").unlink(missing_ok=True)
    ref, hyp = _words(text), _words(" ".join(x["text"] for x in tr["segments"]))
    if not ref:
        return 1.0, []
    good, extra = 0.0, []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, ref, hyp, autojunk=False).get_opcodes():
        if tag == "equal":
            good += i2 - i1
        elif tag == "replace":
            sim = difflib.SequenceMatcher(None, "".join(ref[i1:i2]), "".join(hyp[j1:j2])).ratio()
            good += (i2 - i1) * (sim if sim >= 0.7 else 0)
            if sim < 0.7 and j2 - j1 > i2 - i1:
                extra += hyp[j1:j2][i2 - i1:]
        elif tag == "insert":
            extra += hyp[j1:j2]
    # The last word must arrive whole: "fishing" cut to "fish" reads as a 90 % match overall but sounds broken.
    if hyp and difflib.SequenceMatcher(None, ref[-1], hyp[-1]).ratio() < 0.85:
        extra.append(f"(fim cortado: '{ref[-1]}' ouvido como '{hyp[-1]}')")
    return good / len(ref), [w for w in extra if len(w) > 1]


MIN_SAID = 0.9
FAIL_SAID = 0.8   # below this, or with extra spoken words, the whole narration is rejected


def voice_for(language):
    """Profile for a language: voice.json 'voices' {"en": id, "pt": id}, falling back to the main 'voice'."""
    cfg = read_json(CONFIG, {}) or {}
    return (cfg.get("voices") or {}).get(language[:2]) or cfg.get("voice", "")


def narrate(text, out_wav, voice=None, max_tries=6, pause_ms=120, log=print, language="en"):
    """Sentence-by-sentence cloning, best of up to max_tries takes each, then gate + join.

    A take passes when it is clean (voice_quality 'take' limits) AND Whisper hears back at least MIN_SAID of
    its words. Returns {"takes": n, "sentences": n, "weak": [indices that never passed], "said": mean accuracy}.
    """
    from .common import ffmpeg, probe
    voice = voice or voice_for(language)
    if not voice:
        raise RuntimeError("sem perfil de voz: rode 'python -m src.voicestudio_client candidates' e escolha uma")
    out_wav = Path(out_wav)
    tmp = out_wav.parent / (out_wav.stem + "_takes")
    tmp.mkdir(parents=True, exist_ok=True)
    sents = split_sentences(text)
    best = {}
    pending = list(range(len(sents)))
    takes = 0
    for attempt in range(max_tries):
        if not pending:
            break
        from .voice_sync import release_gpu
        release_gpu()  # the card is the voice's alone while it generates
        files = []
        for i in pending:
            f = tmp / f"s{i:02d}_t{attempt}.wav"
            speak(_tts_text(sents[i]), f, voice=voice, language=language, seed=1000 * attempt + i + 1, speed=1.0)
            files.append((i, f))
            takes += 1
        for (i, f), m in zip(files, quality([f for _, f in files], "take")):
            said, extra = said_right(f, sents[i], language)
            ok = m["status"] == "PASS" and said >= MIN_SAID and not extra
            score = m["score"] + 6 * said + (2 if said >= MIN_SAID else 0) - 4 * len(extra)
            if i not in best or score > best[i][0]:
                best[i] = (score, f, ok, said, extra)
        pending = [i for i in pending if not best[i][2]]
        log(f"  voz: rodada {attempt + 1}: {len(sents) - len(pending)}/{len(sents)} frases limpas e bem ditas")
    silence = tmp / "pause.wav"
    ffmpeg(["-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", f"{pause_ms / 1000:.3f}", silence], "tts")
    # Each take gets an 8 ms fade-in, 150 ms of safety padding after its last syllable and a 40 ms fade-out
    # inside that padding, so a join never amputates a final consonant or clicks.
    parts = []
    for i in range(len(sents)):
        seg = tmp / f"seg_{i:02d}.wav"
        take_dur = probe(best[i][1])["duration"]
        ffmpeg(["-i", best[i][1], "-af", f"afade=t=in:d=0.008,apad=pad_dur=0.15,afade=t=out:st={take_dur + 0.11:.3f}:d=0.04",
                "-ar", "48000", "-ac", "1", seg], "tts")
        parts += [seg, silence]
    lst = tmp / "concat.txt"
    lst.write_text("".join("file '%s'\n" % Path(x).resolve().as_posix() for x in parts[:-1]), encoding="utf-8")
    joined = tmp / "joined.wav"
    ffmpeg(["-f", "concat", "-safe", "0", "-i", lst, "-ar", "48000", "-ac", "1", joined], "tts")
    release_gpu()
    shutil.copyfile(joined, out_wav)  # no gate: filters only lower the measured quality of a clean take
    weak = [i for i in range(len(sents)) if not best[i][2]]
    bad = [i for i in weak if best[i][3] < FAIL_SAID or best[i][4]]
    for i in weak:
        log(f"  voz: frase {i + 1} ficou fraca (entendida {best[i][3]:.0%}"
            + (f", falou a mais: {' '.join(best[i][4])}" if best[i][4] else "") + f"): \"{sents[i][:60]}\"")
    if bad:
        raise RuntimeError(f"{len(bad)} frase(s) mal pronunciada(s) pela voz clonada depois de {max_tries} tentativas")
    return {"takes": takes, "sentences": len(sents), "weak": weak,
            "said": round(sum(best[i][3] for i in best) / max(1, len(best)), 3)}


REF_TEXT_EN = ("Today I am going to show you three details almost everybody missed in the trailer. "
               "The second one is the strangest of all, and you will see exactly why.")


def make_language_profile(language="en", tries=6, log=print):
    """Same channel voice, native reference in another language: clone REF_TEXT_EN with the current profile,
    keep the cleanest best-spoken take, and save it as the profile for that language."""
    cfg = read_json(CONFIG, {}) or {}
    base = cfg.get("voice")
    out = DATA / "voices"
    out.mkdir(parents=True, exist_ok=True)
    tmp = WORK / "voices" / f"lang_{language}"
    tmp.mkdir(parents=True, exist_ok=True)
    files = []
    for k in range(tries):
        f = tmp / f"ref_{k}.wav"
        speak(REF_TEXT_EN, f, voice=base, language=language, seed=50 + k, speed=1.0)
        files.append(f)
    scored = []
    for f, m in zip(files, quality(files, "take")):
        said, extra = said_right(f, REF_TEXT_EN, language)
        scored.append((m["score"] + 6 * said - 4 * len(extra), f, m, said))
        log(f"  {f.name}: pesq {m['pesq']} sdr {m['si_sdr']} entendida {said:.0%} {m['status']}")
    _, f, m, said = max(scored, key=lambda x: x[0])
    ref = Path(shutil.copyfile(f, out / f"clipengine_narrador_ref_{language}.wav"))
    prof = make_profile(ref, f"{cfg.get('voice_name', 'ClipEngine Narrador')} {language.upper()}", REF_TEXT_EN, language)
    cfg.setdefault("voices", {})["pt"] = cfg["voices"].get("pt") if cfg.get("voices") else base
    cfg["voices"][language] = prof["id"]
    cfg.setdefault("refs", {})[language] = str(ref)
    write_json(CONFIG, cfg)
    log(f"perfil {language}: {prof['id']} (pesq {m['pesq']}, entendida {said:.0%}) -> {CONFIG}")
    return prof["id"]


def make_reference(description, key, n=6, log=print, text=None, language="pt-BR"):
    """Best of n voice-design takes of REF_TEXT, gated. Returns (path, metrics)."""
    out = WORK / "voices" / key
    out.mkdir(parents=True, exist_ok=True)
    from .voice_sync import release_gpu
    release_gpu()  # voice design needs the whole card
    files = []
    for k in range(n):
        f = out / f"design_{k}.wav"
        speak(text or REF_TEXT, f, voice="default", description=description, language=language, seed=k + 1, speed=1.0)
        files.append(f)
    ms = quality(files, "take")
    best = max(ms, key=lambda m: m["score"])
    ref = Path(shutil.copyfile(best["file"], out / "reference.wav"))
    log(f"{key}: melhor de {n} = {Path(best['file']).name} (pesq {best['pesq']}, sdr {best['si_sdr']})")
    return ref, best



def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("start")
    sub.add_parser("profiles")
    lp = sub.add_parser("language-profile")
    lp.add_argument("--lang", default="en")
    c = sub.add_parser("candidates")
    c.add_argument("--text", default=None)
    c.add_argument("--lang", default="pt", choices=["pt", "en"])
    m = sub.add_parser("make-profile")
    m.add_argument("wav")
    m.add_argument("--name", default="ClipEngine Narrador")
    m.add_argument("--text", default=SAMPLE)
    a = ap.parse_args()
    if a.cmd == "status":
        say(f"home: {HOME} | instalado: {installed()} | health: {health()} | config: {config()}")
    elif a.cmd == "start":
        say("ok" if start() else f"backend não subiu: tail -n 30 {LOGS / 'voicestudio_backend.log'}")
    elif a.cmd == "language-profile":
        if not start():
            sys.exit("backend não subiu")
        make_language_profile(a.lang, log=say)
    elif a.cmd == "profiles":
        for p in profiles():
            say(f"{p.get('id')} | {p.get('name')} | {p.get('kind', '')}")
    elif a.cmd == "candidates":
        if not start():
            sys.exit("backend não subiu")
        en = a.lang == "en"
        ref_text, lang_code, sample = (REF_TEXT_EN, "en", a.text or SAMPLE_EN) if en else (REF_TEXT, "pt-BR", a.text or SAMPLE)
        for key, desc in (CANDIDATES_EN if en else CANDIDATES).items():
            ref, _ = make_reference(desc, key, text=ref_text, language=lang_code)
            prof = make_profile(ref, f"cand-{key}", ref_text, lang_code)
            prev = WORK / "voices" / key / "preview.wav"
            info = narrate(sample, prev, voice=prof["id"], log=lambda *_: None, language=lang_code)
            q = quality([prev])[0]
            write_json(WORK / "voices" / key / "candidate.json", {"description": desc, "profile": prof["id"],
                                                                  "reference": str(ref), "quality": q, **info})
            say(f"{q['status']} {key}: pesq {q['pesq']} sdr {q['si_sdr']} piso {q['noise_floor_db']} -> {prev}")
    elif a.cmd == "make-profile":
        if not start():
            sys.exit("backend não subiu")
        src = Path(a.wav)
        cand = read_json(src.parent / "candidate.json", None) if src.name in ("preview.wav", "reference.wav") else None
        if cand:  # chosen from `candidates`: reuse its clean reference
            src, text = Path(cand["reference"]), REF_TEXT
        else:
            text = a.text
            q = quality([src], "take")[0]
            if q["status"] != "PASS":
                sys.exit(f"referência reprovada ({', '.join(q['fails'])}): use uma gravação limpa")
        p = make_profile(src, a.name, text)
        cfg = read_json(CONFIG, {}) or {}
        cfg.update(engine="voicestudio", model="voxcpm2", voice=p.get("id") or a.name, voice_name=a.name,
                   ref_wav=str(src), speed=1.0, num_step=16)
        write_json(CONFIG, cfg)
        say(f"perfil criado: {p.get('id')} '{a.name}' -> {CONFIG}")

if __name__ == "__main__":
    main()
