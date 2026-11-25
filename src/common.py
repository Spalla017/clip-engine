"""Shared paths, game profiles, quiet process runner and ffmpeg helpers (Windows 11 + NVENC)."""
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # Windows consoles default to cp1252
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
ENGINE = ROOT / "remotion-engine"
GAMES = ROOT / "games"
ASSETS = ROOT / "assets"
FOOTAGE = ASSETS / "footage"
SFX = ASSETS / "sfx"
WORK = ROOT / "work"
OUTPUT = ROOT / "output"
LOGS = ROOT / "logs"
DATA = ROOT / "data"
HISTORY = DATA / "history.json"

for _d in (WORK, OUTPUT, LOGS, DATA, FOOTAGE, SFX):
    _d.mkdir(parents=True, exist_ok=True)

# Shorts accept up to 3 min; TikTok Creator Rewards pays only videos longer than 1 min.
PLATFORMS = {
    "shorts": {"min": 15.0, "max": 180.0, "script_seconds": (35, 50)},
    "tiktok": {"min": 61.5, "max": 180.0, "script_seconds": (62, 68)},
    # Silent loop comparison of the model Shorts (2026-11-18: 9-11 s pieces made 4-6.5 M views). Only flow_compare_pairs.
    "shorts_loop": {"min": 8.0, "max": 20.0, "script_seconds": (0, 0)},
}


# Output language of every video (script, captions, narration, package text). Chat stays in Portuguese.
LANGS = {
    "en": {"name": "American English (en-US)", "whisper": "en", "edge_voice": "en-US-AndrewNeural", "edge_rate": "+8%",
           "vs_lang": "en", "wps": 3.1, "source": "Source", "sources": "Sources", "title": "TITLE",
           "description": "DESCRIPTION",
           "audience": "a global, US-first audience: US time zones (ET/PT), dollars, imperial units, American spelling"},
    "pt": {"name": "português do Brasil (pt-BR)", "whisper": "pt", "edge_voice": "pt-BR-AntonioNeural", "edge_rate": "+15%",
           "vs_lang": "pt-BR", "wps": 2.9, "source": "Fonte", "sources": "Fontes", "title": "TÍTULO",
           "description": "DESCRIÇÃO",
           "audience": "público brasileiro: horário de Brasília, reais, sistema métrico"},
}
DEFAULT_LANG = "en"


def localize(game, lang):
    """Profile fields written per language ({"en": ..., "pt": ...}) become plain values for the chosen one."""
    for key in ("say_as", "honest_labels", "hashtags", "notes", "news_lang"):
        value = game.get(key)
        if isinstance(value, dict) and set(value) & set(LANGS):
            game[key] = value.get(lang) or value.get(DEFAULT_LANG) or next(iter(value.values()))
    game["lang"] = lang
    return game


def slugify(text, limit=48):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:limit] or "video"


def read_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_env():
    """Load KEY=VALUE pairs from clip-engine/.env, then ~/.config/watch/.env. Never prints values."""
    for env in (ROOT / ".env", Path.home() / ".config" / "watch" / ".env"):
        if not env.exists():
            continue
        for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_game(name):
    """Game profile: names, subreddits, search terms, footage tags, publisher rules."""
    path = GAMES / f"{name}.json"
    if not path.exists():
        have = ", ".join(p.stem for p in GAMES.glob("*.json") if not p.stem.startswith("_"))
        sys.exit(f"Game profile not found: {path}. Available: {have}. Copy games/_template.json to add one.")
    game = read_json(path)
    game["id"] = name
    return game


def run(cmd, log_name=None, check=True, timeout=None, cwd=None):
    """Run a command quietly. Full output goes to logs/<log_name>.log; returns CompletedProcess."""
    cmd = [str(c) for c in cmd]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, cwd=cwd)
    if log_name:
        with open(LOGS / f"{log_name}.log", "a", encoding="utf-8") as f:
            f.write(f"\n$ {' '.join(cmd)}\n{proc.stdout}\n{proc.stderr}\n")
    if check and proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-8:]
        raise RuntimeError(f"{Path(cmd[0]).name} failed ({proc.returncode}):\n" + "\n".join(tail))
    return proc


def ffmpeg(args, log_name="ffmpeg", check=True):
    return run(["ffmpeg", "-y", "-hide_banner", "-v", "error", *args], log_name, check)


def probe(path):
    """Duration, size, fps, audio presence and container tags of a media file."""
    out = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path]).stdout
    data = json.loads(out)
    info = {"duration": float(data["format"].get("duration", 0) or 0), "tags": data["format"].get("tags", {}),
            "has_audio": False, "width": 0, "height": 0, "fps": 0.0, "vcodec": "", "stream_tags": {}}
    for s in data["streams"]:
        if s["codec_type"] == "video" and not info["width"]:
            num, den = (s.get("avg_frame_rate") or "0/1").split("/")
            info.update(width=s["width"], height=s["height"], vcodec=s.get("codec_name", ""),
                        fps=round(float(num) / float(den), 3) if float(den) else 0.0)
        if s["codec_type"] == "audio":
            info["has_audio"] = True
        info["stream_tags"].update(s.get("tags", {}))
    return info


_NVENC = None


def nvenc_ok():
    """True when h264_nvenc really encodes on this machine (driver present, GPU free)."""
    global _NVENC
    if _NVENC is None:
        test = ffmpeg(["-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.2", "-c:v", "h264_nvenc",
                       "-f", "null", "-"], check=False)
        _NVENC = test.returncode == 0
    return _NVENC


# Layout canvas (authored at 1080x1920). Render output (user law 2026-11-22): final 1440x2560 @ 60 fps = scale 4/3; drafts 540x960 @ 30 = CE_DRAFT=1 (see pipeline.py).
OUT_W, OUT_H = 1080, 1920
SRC_MAX_H = 2160  # cuts keep up to 4K so a 9:16 crop is ~1215x2160, not a 608x1080 crop stretched 2.4x


def venc(quality=19, maxrate="12M", preset="veryfast"):
    """Video encoder args: NVENC on the RTX, libx264 as fallback."""
    if nvenc_ok():
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", str(quality), "-b:v", "0",
                "-maxrate", maxrate, "-bufsize", "48M", "-pix_fmt", "yuv420p"]
    return ["-c:v", "libx264", "-preset", preset, "-crf", str(quality), "-maxrate", maxrate,
            "-bufsize", "48M", "-pix_fmt", "yuv420p"]


def cut(src, dst, start, dur, audio=True, fps=None, height=None):
    """Frame-accurate re-encoded cut. audio=False removes the track (-an): trailers and gameplay."""
    vf = []
    if height:
        vf.append(f"scale=-2:{height}")
    if fps:
        vf.append(f"fps={fps}")
    # Fast seek 8 s early, then exact trim on the output side: input-seeking straight into a live
    # recording (MPEG-TS) left the video track starting 4.7 s after the audio, and Remotion ignores that offset.
    pre = min(8.0, start)
    args = ["-ss", f"{start - pre:.3f}", "-i", src, "-ss", f"{pre:.3f}", "-t", f"{dur:.3f}"]
    if vf:
        args += ["-vf", ",".join(vf)]
    args += venc()
    args += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000"] if audio else ["-an"]
    args += ["-map_metadata", "-1", "-movflags", "+faststart", dst]
    ffmpeg(args, "cut")
    if audio:  # A/V must both start at 0, or the render drifts out of sync
        st = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,start_time", "-of", "csv=p=0", str(dst)],
                            capture_output=True, text=True).stdout.split()
        t = {l.split(",")[0]: float(l.split(",")[1]) for l in st if "," in l and l.split(",")[1] not in ("", "N/A")}
        if abs(t.get("video", 0) - t.get("audio", 0)) > 0.1:
            raise RuntimeError(f"cut A/V offset {t} in {dst}")
    return dst


def history_load():
    return read_json(HISTORY, [])


def history_add(entry):
    items = history_load()
    entry["at"] = time.strftime("%Y-%m-%d %H:%M")
    items.append(entry)
    write_json(HISTORY, items)


def say(msg):
    print(msg, flush=True)
