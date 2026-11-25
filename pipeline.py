#!/usr/bin/env python
"""ClipEngine: game shorts for YouTube Shorts and TikTok, any game (profiles in games/*.json).

Flows (all pass the pre-flight and post-render compliance gates):
  python pipeline.py --topic "Tema" [--script-file roteiro.json]            news / theory short
  python pipeline.py --translate-url URL [--script-file roteiro.json]       foreign video -> PT-BR analysis
  python pipeline.py --clip-url URL --clips 3 [--segments-file cortes.json] cuts of a long video
  python pipeline.py --auto-hype N                                          radar -> N topic shorts (needs an LLM key)
  python pipeline.py --audit FILE.mp4 [--visual pass|fail --visual-notes ""] post-render gate + release

Common: --game gta6  --target-platform shorts|tiktok  --layout classic|split  --variations K
        --lang en|pt  --voice edge|clone  --fps 30  --caption-style bold|bounce|clean

Two brains: with --script-file / --segments-file Claude Code is the director (no API). Without them the
script, translation and scoring come from Gemini Flash (GEMINI_API_KEY) or Ollama (OLLAMA_MODEL).
"""
import argparse
import os
import random
import re
import shutil
import subprocess
import sys
from pathlib import Path

from src import asset_miner, compliance_guard as guard, llm_director as llm, sfx_palette, voice_sync
from src.common import (OUT_W, OUT_H, SRC_MAX_H, DATA, ROOT, DEFAULT_LANG, LANGS, localize, nvenc_ok, ENGINE, FOOTAGE, LOGS, OUTPUT, PLATFORMS, SRC, WORK, cut, ffmpeg, history_add, load_env,
                        load_game, probe, read_json, run, say, slugify, venc, write_json)

COMPOSITIONS = {"classic": "ClassicViralShort", "split": "SplitAndZoomShort", "streamer": "StreamerShort"}


# ---------------------------------------------------------------- shared steps

def run_lock():
    """One heavy run at a time: the 8 GB card and the job folders do not survive two pipelines at once."""
    import atexit
    import os
    lock = DATA / "run.lock"
    if lock.exists():
        pid = lock.read_text().strip()
        alive = pid.isdigit() and pid in subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                                                         capture_output=True, text=True).stdout
        if alive:
            sys.exit(f"Outro pipeline está rodando (PID {pid}). Espere terminar; rodar dois juntos estoura a placa de vídeo.")
    lock.write_text(str(os.getpid()))
    atexit.register(lambda: lock.unlink(missing_ok=True))



def demo_footage(game_id, n=8):
    """Synthetic test clips (ffmpeg generators) so the pipeline can be smoke-tested without game video."""
    game = load_game(game_id)
    tag = (game.get("safe_opening_tags") or ["demo"])[0]
    out = FOOTAGE / game_id / tag
    out.mkdir(parents=True, exist_ok=True)
    sources = ["testsrc2=s=1280x720:r=30", "mandelbrot=s=1280x720:r=30", "life=s=320x180:mold=10:r=30:ratio=0.1:death_color=#C83232:life_color=#00ff00,scale=1280:720:flags=neighbor",
               "gradients=s=1280x720:r=30:speed=0.08", "cellauto=s=1280x720:r=30"]
    for i in range(n):
        dst = out / f"demo_{i:02d}.mp4"
        if not dst.exists():
            ffmpeg(["-f", "lavfi", "-i", sources[i % len(sources)], "-t", "4", *venc(), "-an", dst], "demo")
            write_json(dst.with_suffix(".json"), {"kind": "licensed", "tag": tag, "game": game_id, "dur": 4.0,
                                                  "source_title": "padrão de teste sintético (ffmpeg)", "source_url": ""})
    say(f"demo footage: {n} clips -> {out}")


def pick_broll(game_id, tags, duration, seed=0, anchors=None, fps=60, sources=None, opener=None, horizontal_only=False,
               max_shot=5.0, sections=None, tag_order=None):
    """Clips in script-tag order until the narration is covered. A shot never appears twice in one video.

    Two pieces of the same original shot (a long take split in two) count as the same shot.
    anchors: [(seconds, tag)] forces a clip of that tag to start when the narration reaches that moment
    sections: [(seconds, tag)] from that moment on, filler shots come from this tag (list videos, 2026-11-19:
    "Number two" opens the map beat, then every shot stays on item two's footage until the next section)
    (the "Welcome to The Keys" sign when the script says "Leonida Keys").
    sources: only clips whose source_title contains one of these strings (e.g. only the current trailers).
    opener: file name (stem) of the hero shot that opens the video (the "one strong scene" format).
    max_shot: longest time one clip stays on screen (model Shorts: 1.5-2.5 s, law since 2026-11-18).
    tag_order: {tag: [clip stems]} plays those clips in this order, and only them (2026-11-20, easter-egg list:
    shuffled clips showed the wake-up before the UFO and the falling egg before the climb).
    """
    clips = asset_miner.library(game_id)
    if horizontal_only:  # "card" layout shows a 16:9 window: vertical official Shorts would lose most of the frame
        clips = [c for c in clips if not c["meta"].get("vertical")]
    if sources:
        clips = [c for c in clips if any(s.lower() in (c["meta"].get("source_title") or "").lower() for s in sources)]
    if not clips:
        sys.exit(f"Sem footage em assets/footage/{game_id}/. Rode /minerar-broll (python -m src.asset_miner ...) "
                 "ou use --demo-footage para um teste com padrões sintéticos.")
    rng = random.Random(seed)
    # Shot ids are per source video: two trailers can both have a "shot 5".
    shot = lambda c: (c["meta"].get("source_url", ""), c["meta"].get("shot") or c["path"])
    by_tag = {}
    for c in clips:
        by_tag.setdefault(c["meta"].get("tag", ""), []).append(c)
    for pool in by_tag.values():
        rng.shuffle(pool)
    fixed = set()
    for t, stems in (tag_order or {}).items():
        byname = {Path(c["path"]).stem: c for c in by_tag.get(t, [])}
        missing = [x for x in stems if x not in byname]
        if missing:
            sys.exit(f"tag_order: clipes não encontrados em {t}: {missing[:3]}")
        by_tag[t] = [byname[x] for x in stems]
        fixed.add(t)
    # Tags listed in the profile (ex.: armed scenes, single-use signs) only enter when asked for by name.
    game_cfg = load_game(game_id)
    skip = set(game_cfg.get("fallback_exclude_tags", []))
    rest = [c for c in clips if c["meta"].get("tag") not in skip] or clips[:]
    rng.shuffle(rest)
    calm = [t for t in game_cfg.get("safe_opening_tags", []) if t in by_tag]
    order = list(tags or []) or (calm + [t for t in by_tag if t not in skip and t not in calm]) or list(by_tag)
    # tag "*" = no forced scene: back to the storyboard order (end of a card about another game)
    anchors = sorted((float(t), tag) for t, tag in (anchors or []) if tag in by_tag or tag == "*")
    picked, total, i, used, ai = [], 0.0, 0, set(), 0
    if opener:
        hero = next((c for c in clips if Path(c["path"]).stem == opener), None)
        if not hero:
            sys.exit(f"Cena de abertura não encontrada: {opener}")
        clip = dict(hero)
        clip["dur"] = min(float(clip["dur"]), max_shot)
        used.add(shot(clip))
        picked.append(clip)
        total += max(2, round(clip["dur"] * fps) - 1) / fps
    while total < duration + 0.5:
        forced = None
        if ai < len(anchors) and picked:
            # Less than a second before an anchored scene: stretch the clip on screen instead of
            # squeezing in another one, so the anchored clip starts exactly on its cue.
            gap = anchors[ai][0] - total
            room = float(picked[-1]["meta"].get("dur") or 0) - picked[-1]["dur"]
            if 0 < gap < 1.0 and room >= gap:
                before = max(2, round(picked[-1]["dur"] * fps) - 1) / fps
                picked[-1]["dur"] = round(picked[-1]["dur"] + gap, 3)
                total += max(2, round(picked[-1]["dur"] * fps) - 1) / fps - before
        # Within half a second of the cue the anchored clip starts now (a little early); a filler shot here
        # would push it up to 1.5 s late (seen 2026-11-18: "GTA 5 has 69" landed on GTA 6 footage).
        if ai < len(anchors) and total >= anchors[ai][0] - 0.5:
            forced = anchors[ai][1]
            ai += 1
        sec = [g for t0, g in sorted(sections or []) if t0 - 0.5 <= total]
        tag = forced if forced and forced != "*" else (sec[-1] if sec and sec[-1] in by_tag else order[i % len(order)])
        pool = [c for c in by_tag.get(tag, []) if shot(c) not in used] or [c for c in rest if shot(c) not in used]
        if not pool:
            have = sum(min(float(c["dur"]), 5.0) for c in {shot(c): c for c in rest}.values())
            sys.exit(f"Footage insuficiente para {duration:.0f} s sem repetir cena: há ~{have:.0f} s de tomadas "
                     f"únicas em assets/footage/{game_id}/. Minere mais vídeos oficiais (/minerar-broll) ou encurte o roteiro.")
        if picked and len(pool) > 1 and tag not in fixed:
            # 2026-11-19 (our military-base Short: the same black car in 5 shots in a row): prefer a clip that is not
            # the next few seconds of the same source as the previous one; an anchored tag keeps its order otherwise.
            pm = picked[-1]["meta"]
            def _near(c, pm=pm):
                m = c["meta"]
                try:
                    return m.get("source_url") == pm.get("source_url") and abs(float(m.get("start", 0)) - float(pm.get("start", 0))) < 20
                except (TypeError, ValueError):
                    return False
            pool = sorted(pool, key=_near)  # stable: far-away clips first, order kept within each group
        clip = dict(pool[0])
        clip["dur"] = min(float(clip["dur"]), max_shot)
        if ai < len(anchors):  # end this clip where the next anchored scene has to start
            gap = anchors[ai][0] - total
            if 1.0 <= gap < clip["dur"]:
                clip["dur"] = round(gap, 3)
            elif 0.5 <= gap < 1.0:  # a short filler shot that ends exactly on the anchored word
                clip["dur"] = round(gap, 3)
            elif 0 < gap < 0.5:
                clip["dur"] = min(clip["dur"], 1.5)
        used.add(shot(clip))
        picked.append(clip)
        total += max(2, round(clip["dur"] * fps) - 1) / fps  # same arithmetic as BrollTrack
        if not forced or forced == "*":
            i += 1
    return picked


_ORDINALS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
             "um": 1, "dois": 2, "três": 3, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6, "sete": 7, "oito": 8, "nove": 9,
             "dez": 10}


def list_items(script, captions, game):
    """Enumerations in the script ("Number two: the Leonida Keys.") -> [{n, index, label, at, dur, tag}].

    Each item becomes a compact card on screen; when the profile maps its label to a footage tag
    (label_tags), the B-roll of that scene is anchored to the same moment.
    """
    found = re.findall(r"\b(?:number|n[uú]mero)\s+([a-zà-ú]+|\d+)\s*[:,–-]\s*(?:the\s+|o\s+|a\s+|os\s+|as\s+)?([^.!?:;,]{2,44})",
                       script, flags=re.I)
    items = []
    key = lambda t: re.sub(r"[^a-z0-9à-ú]", "", t.lower())
    pos = 0
    label_tags = {k.lower(): v for k, v in (game.get("label_tags") or {}).items()}
    for ordinal, label in found:
        n = int(ordinal) if ordinal.isdigit() else _ORDINALS.get(ordinal.lower())
        if not n:
            continue
        at = None
        for j in range(pos, len(captions) - 1):
            if key(captions[j]["text"]) in ("number", "numero", "número") and key(captions[j + 1]["text"]) in (ordinal.lower(), str(n)):
                at, pos = captions[j]["startMs"] / 1000, j + 2
                break
        if at is None:
            continue
        label = " ".join(label.split())
        tag = None
        for name, t in label_tags.items():
            if name in label.lower():
                label, tag = name, t
                break
        items.append({"n": n, "index": f"{n:02d}", "label": label.upper(), "at": round(at, 2), "tag": tag})
    for k, it in enumerate(items):
        nxt = items[k + 1]["at"] if k + 1 < len(items) else it["at"] + 3.2
        it["dur"] = round(max(1.6, min(3.2, nxt - it["at"] - 0.25)), 2)
    return items


def broll_props(refs, clips):
    """Remotion props. hide_bottom (burned-in UI bar at the foot of a trailer shot) becomes a top-anchored zoom."""
    out = []
    for ref, c in zip(refs, clips):
        hide = float(c["meta"].get("hide_bottom", 0) or 0)
        bars = float(c["meta"].get("letterbox", 0) or 0)  # black bar on each of top and bottom (cinema-format trailers)
        if bars:
            out.append({"src": ref, "dur": c["dur"], "zoom": round(1 / (1 - 2 * min(bars + hide / 2, 0.3)), 3), "origin": "center"})
        else:
            out.append({"src": ref, "dur": c["dur"], "zoom": round(1 / (1 - min(hide, 0.4)), 3) if hide else 1})
    return out


def stage(job, items, prefix):
    """Copy media into the job folder; return Remotion 'media:' references.

    The staged name comes from the source file itself (path + size + mtime), never from its position:
    position-based names made a second run with another clip order render stale clips.
    """
    import hashlib
    media = job / "media"
    media.mkdir(parents=True, exist_ok=True)
    refs = []
    for it in items:
        src = Path(it["path"]).resolve()
        st = src.stat()
        tag = hashlib.sha1(f"{src}|{st.st_size}|{int(st.st_mtime)}".encode()).hexdigest()[:10]
        dst = media / f"{prefix}_{tag}{src.suffix}"
        if not dst.exists():
            shutil.copyfile(src, dst)
        refs.append(f"media:media/{dst.name}")
    return refs


def sfx_cues(job, cut_times, duration):
    """Sound effects under a narration: low on purpose (0.25-0.30 for hits, whooshes lower) so the voice stays on top."""
    pal = sfx_palette.ensure_palette()
    refs = stage(job, [{"path": pal[n]} for n in ("impacto", "whoosh", "pop")], "sfx")
    cues = [{"src": refs[0], "at": 0.0, "volume": 0.28}, {"src": refs[2], "at": 3.5, "volume": 0.25}]
    cues += [{"src": refs[1], "at": max(0.0, t - 0.12), "volume": 0.13} for t in cut_times[1::2] if 1.0 < t < duration - 1.0]
    return cues


def render(job, jobs):
    import json as _json
    # Same jobs as the last successful render and outputs still on disk: reuse them.
    # The key also covers the staged media and the Remotion code: same props with a recut clip or an edited
    # component must render again (bug 2026-11-16: a fixed credit position was skipped as "same jobs").
    import hashlib
    files = [f for d in (job / "media", ENGINE / "src") if d.exists() for f in sorted(d.rglob("*")) if f.is_file()]
    # User law (2026-11-22): FINAL = 1440x2560 @ 60 fps always (scale 4/3 of the 1080x1920 layouts). Draft/preview = CE_DRAFT=1 -> 540x960 @ 30.
    scale = os.environ.get("CE_SCALE") or ("0.5" if os.environ.get("CE_DRAFT") == "1" else "1.3333333333333333")
    finger = hashlib.sha1(("|".join(f"{f}:{f.stat().st_size}:{f.stat().st_mtime_ns}" for f in files) + f"|scale={scale}").encode()).hexdigest()
    same = read_json(job / "jobs.json", None) == _json.loads(_json.dumps(jobs))
    ok = job / "render.ok"
    if same and ok.exists() and ok.read_text().strip() == finger and all((job / j["output"]).exists() for j in jobs):
        say("  render reaproveitado (props, mídia e código iguais)")
        return
    (job / "render.ok").unlink(missing_ok=True)
    write_json(job / "jobs.json", jobs)
    log = LOGS / f"render_{job.name}.log"
    with open(log, "w", encoding="utf-8") as f:
        code = subprocess.run(["node", str(ENGINE / "render.mjs"), "--jobs", str(job / "jobs.json"), "--root", str(job), "--scale", scale],
                              stdout=f, stderr=subprocess.STDOUT, cwd=str(ENGINE)).returncode
    if code != 0:
        tail = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-6:]
        sys.exit("Render falhou (log: %s):\n%s" % (log, "\n".join(t[:400] for t in tail)))
    (job / "render.ok").write_text(finger)


def finalize(src, dst):
    """Final export: NVENC H.264, -14 LUFS, clean metadata, faststart."""
    ffmpeg(["-i", src, *venc(17, "24M", "slow"), "-af", "loudnorm=I=-14:TP=-1.5:LRA=11,alimiter=limit=0.79:level=false:attack=2:release=50", "-c:a", "aac", "-b:a", "192k",
            "-ar", "48000", "-map_metadata", "-1", "-map_chapters", "-1", "-fflags", "+bitexact",
            "-movflags", "+faststart", dst], "finalize")
    return dst


def package_text(meta, game):
    L = LANGS[game.get("lang", DEFAULT_LANG)]
    tags = " ".join(game.get("hashtags", []))
    lines = [f"{L['title']}: {meta.get('title', '')}", "", f"{L['description']}: {meta.get('description', '')}", "", tags]
    if meta.get("credit"):
        lines += ["", meta["credit"] + (f" ({meta['source_url']})" if meta.get("source_url") else "")]
    if meta.get("sources"):
        lines += ["", f"{L['sources']}: " + "; ".join(meta["sources"])]
    # Tags and hashtags from this week's radar (python -m src.trend_panel --game "<name>"), when it covers this game.
    from src.trend_panel import seo_pack
    seo = seo_pack(game.get("name", ""))
    if seo:
        lines += ["", "HASHTAGS YOUTUBE: " + " ".join(seo["hashtags_youtube"]),
                  "HASHTAGS TIKTOK: " + " ".join(seo["hashtags_tiktok"]),
                  "TAGS (YouTube, hidden field): " + ", ".join(seo["hidden_tags"])]
    return "\n".join(lines)


def release(final, cert_path, cert, game, meta):
    """Only approved videos reach output/."""
    name = meta["name"]
    if cert["status"] == "APPROVED" and meta.get("facts") == "unverified":
        # Gate passed, but the script was written by the LLM alone from a headline. Tests on 2026-11-13 showed
        # invented details and outdated claims in 3 of 3 such scripts, all approved by the visual check.
        say(f"[PENDING_FACTS] {final}")
        say(f"  roteiro escrito pelo LLM sem conferência: leia {Path(final).with_name('plan_v1.json')} e confira cada fato")
        say(f"  depois: python pipeline.py --audit \"{final}\" --game {game['id']} --visual pass --facts ok")
        return False
    if cert["status"] != "APPROVED":
        say(f"[{cert['status']}] {final}")
        if cert["status"] == "PENDING_VISUAL":
            say(f"  veja a folha: {cert['post_render']['sheet']}")
            say(f"  depois: python pipeline.py --audit \"{final}\" --game {game['id']} --target-platform {cert['post_render']['platform']} --visual pass|fail")
        else:
            for stage_key in ("pre_flight", "post_render"):
                for c in cert[stage_key].get("checks", []):
                    if not c["ok"]:
                        say(f"  x {c['rule']}: {c['detail']}")
            if cert["post_render"].get("visual", {}).get("verdict") == "fail":
                say(f"  x visual: {cert['post_render']['visual'].get('notes', '')}")
        return False
    out = OUTPUT / f"{name}.mp4"
    shutil.copyfile(final, out)
    shutil.copyfile(cert_path, OUTPUT / f"{name}_compliance.json")
    (OUTPUT / f"{name}.txt").write_text(package_text(meta, game), encoding="utf-8")
    history_add({"name": name, "topic_slug": meta.get("topic_slug", ""), "title": meta.get("title", ""),
                 "game": game["id"], "flow": meta.get("flow", ""), "file": str(out)})
    say(f"[APPROVED] {out}")
    return True


def gate_post(final, pre, game, platform, meta, visual_mode):
    post = guard.postrender(final, game, platform)
    if post["status"] == "PENDING_VISUAL" and visual_mode == "auto":
        verdict = guard.vision_verdict(post["sheet"], game)
        if verdict:
            post = guard.postrender(final, game, platform, visual=verdict)
    cert_path = Path(final).with_name(Path(final).stem + "_compliance.json")
    cert = guard.certificate(cert_path, pre, post, {"title": meta.get("title", ""), "flow": meta.get("flow", ""),
                                                    "game": game["id"], "meta": meta})
    return release(final, cert_path, cert, game, meta)


def gate_pre(job, plan, game, platform, tag=""):
    report, plan = guard.preflight(plan, game, platform)
    write_json(job / f"preflight{tag}.json", report)
    write_json(job / f"plan{tag}.json", {k: v for k, v in plan.items() if k != "captions"})
    for fix in report["auto_fixes"]:
        say(f"  pre-flight fix: {fix}")
    return report, plan


def stop_flagged(report, job):
    say(f"PRÉ-FLIGHT: FLAGGED ({job})")
    for c in report["checks"]:
        if not c["ok"]:
            say(f"  x {c['rule']}: {c['detail']}")
    sys.exit(2)


def narrate(job, script, game, a):
    """Narration + captions. Cached by script/voice/language so a second pass (overlays) keeps the same timing."""
    import hashlib
    wav = job / "media" / "narration.wav"
    wav.parent.mkdir(parents=True, exist_ok=True)
    from src.voicestudio_client import config as _vcfg
    key = hashlib.sha1(f"{script}|{game.get('say_as')}|{a.voice}|{a.rate}|{a.tts}|{a.lang}|{_vcfg()}".encode()).hexdigest()
    cached = read_json(job / "narration.json", {}) or {}
    want = "voicestudio" if (a.voice in ("clone", "voxcpm") or a.tts == "voicestudio") else "edge"
    # The engine actually used is part of the cache: an Edge fallback must never be reused as the cloned voice.
    # CE_KEEP_NARRATION=1 (patch mode, 2026-11-20): a re-render after fixing one sentence keeps the patched
    # narration.wav + narration.json even if the cache key moved; never re-voice the whole script for a fix.
    locked = os.environ.get("CE_KEEP_NARRATION") == "1" and wav.exists() and cached.get("captions")
    if locked or (cached.get("key") == key and wav.exists() and cached.get("engine", want) == want):
        return probe(wav)["duration"], voice_sync.align_to_script(cached["captions"], script)
    # Spoken text only: "GTA 6" is read as "GTA six"; captions keep the script's spelling (same word count).
    spoken = script
    for written, said in (game.get("say_as") or {}).items():
        spoken = re.sub(r"(?<!\w)" + re.escape(written) + r"(?!\w)", said, spoken)
    dur, engine = voice_sync.synthesize(spoken, wav, a.voice, a.rate, a.tts, a.lang)
    say(f"  voz: {engine}, {a.lang}, {dur:.1f} s")
    tr = voice_sync.transcribe_file(wav, job / "narration.json", language=LANGS[a.lang]["whisper"],
                                    prompt=game.get("whisper_prompt"))
    aligned = voice_sync.align_to_script(tr["captions"], script)
    write_json(job / "narration.json", {**tr, "key": key, "aligned": aligned, "engine": engine})
    return dur, aligned


def apply_voice_gap(job, captions, dur, gap):
    """voice_gap (2026-11-23, 'GPS' beat of gta5-refs-gta6): the narration stops for `seconds` after `after_word`, the
    gameplay carries the beat with its own sound (staged as an sfx cue) and the music rises (AudioLayer ducking).
    Writes media/narration_gap.wav (the cached narration.wav stays untouched), shifts every later caption.
    gap = {"after_word", "seconds", "nth"=1, "game_audio": {"src": <video>, "ss": <s>} | false}.
    Returns (dur, captions, window (t0, t1))."""
    word, secs, nth = str(gap["after_word"]).lower(), float(gap["seconds"]), int(gap.get("nth", 1))
    idx = [i for i, c in enumerate(captions) if re.sub(r"\W", "", c["text"].lower()) == word]
    if len(idx) < nth:
        sys.exit(f"voice_gap: palavra '{word}' (#{nth}) não encontrada na narração")
    i = idx[nth - 1]
    end = captions[i]["endMs"] / 1000
    nxt = captions[i + 1]["startMs"] / 1000 if i + 1 < len(captions) else end + 0.3
    cut = round(end + min(0.12, max(0.0, nxt - end) / 2), 3)  # cut in the natural pause, never inside the word
    src, out = job / "media" / "narration.wav", job / "media" / "narration_gap.wav"
    pr = subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                                  "stream=sample_rate,channels", "-of", "csv=p=0", str(src)], text=True).strip().split(",")
    sr, ch = pr[0], "mono" if pr[1] == "1" else "stereo"
    fc = (f"[0:a]atrim=0:{cut},asetpts=PTS-STARTPTS[a];anullsrc=r={sr}:cl={ch},atrim=0:{secs},asetpts=PTS-STARTPTS[s];"
          f"[0:a]atrim=start={cut},asetpts=PTS-STARTPTS[b];[a][s][b]concat=n=3:v=0:a=1[o]")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-filter_complex", fc, "-map", "[o]", str(out)], check=True)
    shift = round(secs * 1000)
    new = [dict(c, startMs=c["startMs"] + shift, endMs=c["endMs"] + shift) if j > i else dict(c) for j, c in enumerate(captions)]
    ga = gap.get("game_audio")
    if ga:
        gwav = job / "media" / "gameaudio.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(ga["ss"]), "-i", str(ga["src"]), "-t", str(secs), "-vn", "-ac", "2",
                        "-ar", "48000", "-af", "afade=t=in:d=0.15,afade=t=out:st=%s:d=0.3" % (secs - 0.3), str(gwav)], check=True)
    say(f"  voice_gap: {secs:.1f} s de silêncio após '{word}' em {cut:.2f} s (legendas seguintes +{secs:.1f} s)")
    return dur + secs, new, (cut, cut + secs)


# Caption rules for every cut (user, 2026-11-23): a word shows at most 0.7 s (Whisper stretches words over silence and
# the last caption froze on screen for 9 s) and swear words are censored on screen (the audio is the streamer's).
MAX_WORD_MS = 700
CENSOR = re.compile(r"\b(sh)(it|itty|itting)\b|\b(f)(uck\w*|\*+)|\b(b)(itch\w*)|\b(a)(ss|sshole)\b|\b(d)(amn)\b|\b(n)(igg\w*)", re.I)


def censor(text):
    """'shit' -> 'S***', 'f***'/'fuck' -> 'F***': first letter kept, the rest masked."""
    return CENSOR.sub(lambda m: m.group(0)[0] + "***", text)


def clean_caps(caps):
    """Cap each word's on-screen time and censor swear words; renderer then hides captions in gaps > 600 ms."""
    return [{**c, "text": censor(c["text"]), "endMs": min(c["endMs"], c["startMs"] + MAX_WORD_MS)} for c in caps]


def captions_in_language(tr, seg, game, lang):
    """Cut flow: when the source speech is not in the output language, caption it translated.

    Each translated sentence is spread evenly over the time of the original sentence. Returns None when the
    source already is in the output language or no LLM is available (then the original words are used).
    """
    if (tr.get("language") or "")[:2] == LANGS[lang]["whisper"]:
        return None
    segs = [s for s in tr["segments"] if s["end"] > seg["start"] and s["start"] < seg["end"]]
    try:
        done = llm.translate_batch(segs, game)
    except llm.NoBrain as e:
        say(f"  legendas ficam no idioma original (sem LLM para traduzir): {str(e)[:80]}")
        return None
    caps = []
    for s in done:
        words = s["tx"].split()
        t0, t1 = max(s["start"], seg["start"]), min(s["end"], seg["end"])
        step = (t1 - t0) / max(1, len(words))
        for k, w in enumerate(words):
            caps.append({"text": " " + w, "startMs": int((t0 + k * step - seg["start"]) * 1000),
                         "endMs": int((t0 + (k + 1) * step - seg["start"]) * 1000)})
    return caps


def load_script(a, game, make):
    """Script JSON from Claude (--script-file) or from the LLM brain."""
    if a.script_file:
        return read_json(a.script_file), False
    return make(), True


def music_bed(a, job):
    """Optional background music: --music FILE | auto (first licensed file in assets/music) | off (default)."""
    choice = getattr(a, "music", "off")
    if choice == "off":
        return None
    folder = ROOT / "assets" / "music"
    files = sorted(p for p in folder.glob("*") if p.suffix.lower() in (".mp3", ".wav", ".m4a", ".ogg")) if folder.exists() else []
    gen = [p for p in files if p.name.startswith("gen_")]  # generated (ACE-Step) tracks first
    path = ((gen or files or [None])[-1] if gen else (files[0] if files else None)) if choice == "auto" else Path(choice)
    if not path or not path.exists():
        if choice == "auto":
            return None
        sys.exit(f"Trilha não encontrada: {choice}")
    meta = read_json(path.with_suffix(".json"), {}) or {}
    return {"path": str(path), "kind": meta.get("kind", "unknown"), "source": meta.get("source", "")}


def base_props(a, game, duration, captions, headline, hook, credit="", job=None, plan=None):
    props = _base_props(a, game, duration, captions, headline, hook, credit)
    props["povText"] = getattr(a, "pov", "") or ""
    music = (plan or {}).get("music")
    if music and job:
        props["musicSrc"] = stage(job, [{"path": music["path"]}], "music")[0]
        props["musicVolume"] = 0.08 if props.get("captions") else 0.14
    return props


def _base_props(a, game, duration, captions, headline, hook, credit=""):
    return {"fps": a.fps, "durationInSeconds": round(duration, 3), "captions": captions,
            "captionStyle": a.caption_style, "headline": headline, "hookLine1": hook[0], "hookLine2": hook[1],
            "accent": game.get("accent", "#FF2E88"), "credit": "", "showProgressBar": True,  # credit/disclaimer NEVER on screen (user law 2026-11-22): it goes to _POST.md / description only
            "cards": [], "zoomTargets": [], "sfx": [], "broll": []}


def extras(a, job, props, game):
    """Optional cards and magnifier targets written by the director (references/formatos-json.md)."""
    data = read_json(a.overlays, {}) if a.overlays else {}
    cards = data.get("cards", [])
    if cards:
        from src.card_maker import make_card
        for i, c in enumerate(cards):
            if not c.get("path"):
                c["path"] = str(make_card(c["text"], c.get("source", ""), job / "media" / f"cardsrc_{i}.png",
                                          game.get("accent", "#FF2E88")))
        refs = stage(job, [{"path": c["path"]} for c in cards], "card")
        props["cards"] = [{"src": r, "at": c["at"], "dur": c["dur"]} for r, c in zip(refs, cards)]
        pop = stage(job, [{"path": sfx_palette.ensure_palette()["pop"]}], "sfxpop")[0]
        props["sfx"] = props.get("sfx", []) + [{"src": pop, "at": c["at"], "volume": 0.35} for c in cards]
    info = data.get("infoCards", [])
    if info:
        # {"image": path, "kind": "flag|photo", "word": "zealand" | "at": s, "dur": 2.2, "title", "date", "time"}
        caps = props.get("captions") or []
        out, warn = [], []
        for c in info:
            at = c.get("at")
            if at is None and c.get("word"):
                hit = next((x for x in caps if re.sub(r"\W", "", x["text"].lower()) == c["word"].lower()), None)
                at = round(hit["startMs"] / 1000 - 0.15, 2) if hit else None
                if at is None: warn.append(c["word"])
            if at is None: continue
            item = {"at": max(0.0, at), "dur": float(c.get("dur", 2.2)), "kind": c.get("kind", "photo")}
            for k in ("title", "date", "time"):
                if c.get(k): item[k] = str(c[k])
            if c.get("image"):
                item["src"] = stage(job, [{"path": c["image"]}], "info")[0]
            out.append(item)
        if warn: say("  aviso: card sem palavra na narração: " + ", ".join(warn))
        total = sum(x["dur"] for x in out); dur = float(props.get("durationInSeconds") or 1)
        if len(out) > 4 or total > 0.25 * dur:
            say(f"  aviso: {len(out)} cards de contexto, {total:.1f} s ({total / dur:.0%} do vídeo): regra é ≤ 4 e ≤ 25 %")
        props["infoCards"] = out
        say("  cards de contexto: " + ", ".join(f"{x.get('title') or x.get('date') or x.get('time')} @{x['at']}s" for x in out))
        pop = stage(job, [{"path": sfx_palette.ensure_palette()["pop"]}], "sfxpop")[0]
        props["sfx"] = props.get("sfx", []) + [{"src": pop, "at": x["at"], "volume": 0.3} for x in out]
    props["zoomTargets"] = data.get("zoomTargets", [])
    ui = data.get("uiCards", [])
    if ui:
        # {"kind": "bar|counter|checklist", "word": "broke" | "at": s, "dur": 2.5, "title", ...kind fields}
        # flow_topic resolves them first (checklist on its spoken items, card of another game shortened when the
        # speech moves on) and keeps the result so the B-roll and the card agree; other flows resolve here.
        out = getattr(a, "ui_resolved", None)
        if out is None:
            out, warn = resolve_ui_cards(ui, props.get("captions") or [])
            if warn: say("  aviso: " + "; ".join(warn))
        out = [{k: v for k, v in c.items() if k != "game"} for c in out]
        props["uiCards"] = out
        say("  cards de interface: " + ", ".join(f"{x['kind']} @{x['at']}s" for x in out))
        pop = stage(job, [{"path": sfx_palette.ensure_palette()["pop"]}], "sfxpop")[0]
        props["sfx"] = props.get("sfx", []) + [{"src": pop, "at": x["at"], "volume": 0.3} for x in out]
    model_overlays(job, props, data)  # after the UI cards: emojis are placed clear of them
    # Layout and game-UI cards from the 2026-11-18 study of viral GTA 6 TikToks (references/refs-tiktok-2026-11-18.md).
    if data.get("layout") in ("full", "card"):
        props["layout"] = data["layout"]
        if data["layout"] == "card":
            props["zoomTargets"] = []   # the magnifier maps screen coordinates of the full layout
    if data.get("headlineFixed"):
        props["headlineFrom"] = 0       # headline on screen from the first frame; replaces the hook text
        props["hookLine1"] = props["hookLine2"] = ""
    if data.get("listBadge"):
        props["listBadge"] = True


STICKERS = ROOT / "assets" / "stickers"


def _word_at(caps, word, nth=1):
    """Start (s) of the nth time `word` is spoken, 0.15 s early so the overlay lands with the word."""
    hits = [x for x in caps if re.sub(r"\W", "", x["text"].lower()) == re.sub(r"\W", "", word.lower())]
    return round(hits[nth - 1]["startMs"] / 1000 - 0.15, 2) if len(hits) >= nth else None


_NUMW = {"1": "one", "2": "two", "3": "three", "4": "four", "5": "five", "6": "six", "7": "seven", "8": "eight",
         "9": "nine", "10": "ten"}
_ITEM_SKIP = {"and", "the", "a", "an", "of", "to", "in", "on", "with", "your", "you"}


def _spoken_at(caps, token, after=0.0, before=1e9):
    """First time (s) `token` is spoken in [after, before); digits and number words match each other."""
    tok = re.sub(r"\W", "", token.lower())
    alts = {tok, _NUMW.get(tok, tok)} | {k for k, v in _NUMW.items() if v == tok}
    for c in caps:
        t = c["startMs"] / 1000
        if after <= t < before and re.sub(r"\W", "", c["text"].lower()) in alts:
            return t
    return None


def resolve_ui_cards(ui, caps):
    """Times of the game-UI cards. A checklist always starts on the first word of its first spoken item, ticks
    each item when it is said (itemAt, seconds from the card start) and stays until 1.2 s after the last one
    (user rule 2026-11-18: the GTA 6 list entered at the closing question, after the items were said).
    "game": the game a card is about ("gta5"); a title naming GTA 5 / GTA V counts as "gta5"."""
    out, warn = [], []
    for c in ui:
        at = c.get("at")
        if at is None and c.get("word"):
            at = _word_at(caps, c["word"], int(c.get("nth", 1)))
            if at is None: warn.append(f"card de interface sem palavra na narração: {c['word']}")
        dur = float(c.get("dur", 2.5))
        item = {k: v for k, v in c.items() if k in ("kind", "title", "value", "left", "right", "from", "to", "prefix", "suffix", "items", "y", "scale")}
        if c.get("kind") == "checklist" and c.get("items") and c.get("at") is None:
            # search from up to 15 s before the card's own word (lists are usually said just before it)
            t0 = max(0.0, at - 15.0) if at is not None else 0.0
            times, prev = [], t0
            for k, it in enumerate(c["items"]):
                # the most distinctive word first: "3 criminals" is found by "criminals", not by any "three"
                toks = sorted((w for w in re.findall(r"[A-Za-z0-9']+", it) if w.lower() not in _ITEM_SKIP),
                              key=lambda w: (w.isdigit() or w.lower() in _NUMW.values(), -len(w)))
                until = (at + 15.0 if at is not None else 1e9) if k == 0 else prev + 12.0
                hit = next((h for h in (_spoken_at(caps, w, prev, until) for w in toks) if h is not None), None)
                if hit is None:
                    warn.append(f"checklist '{c.get('title', '')}': item não falado na narração: {it}")
                times.append(hit)
                prev = hit if hit is not None else prev
            said = [t for t in times if t is not None]
            if said:
                start = round(max(0.0, said[0] - 0.15), 2)
                filled, last = [], start
                for t in times:  # an unspoken item ticks 0.4 s after the previous one
                    last = (t - 0.1) if t is not None else last + 0.4
                    filled.append(round(max(0.0, last - start), 2))
                at, dur = start, round(max(2.0, said[-1] + 1.2 - start), 2)
                item["itemAt"] = filled
        if at is None:
            continue
        game = c.get("game") or ("gta5" if re.search(r"\bGTA ?(5|V)\b", str(c.get("title", "")), re.I) else None)
        item.update({"at": max(0.0, round(at, 2)), "dur": dur, "game": game})
        out.append(item)
    return out, warn


def card_game_anchors(ui, anchors, tagmap):
    """User rule 2026-11-18: while a card about another game is on screen ("GTA 5 STORY MISSIONS"), the B-roll is
    that game's footage. Adds anchors of its tag every 2.4 s of the card and a resume anchor ("*") when it leaves;
    if the narration moves to the main game first (an anchor of another tag inside the card), the card leaves
    there too. Mutates the cards' dur."""
    extra = []
    for c in ui:
        tag = tagmap.get(c.get("game") or "")
        if not tag:
            continue
        s, e = c["at"], c["at"] + c["dur"]
        other = [t for t, g in anchors if s + 0.3 < t < e and g != tag]
        if other:
            c["dur"] = round(max(1.2, min(other) - s), 2)
            e = s + c["dur"]
        t = s
        while t < e - 0.6:
            covered = any(g == tag and t - 2.4 < t0 <= t + 0.3 for t0, g in anchors + extra)
            if not covered:
                extra.append((round(t, 2), tag))
            t += 2.4
        if not other:
            extra.append((round(e, 2), "*"))
    return extra


def _sticker_box(item, cx, cy):
    w = float(item.get("w", 420))
    if item.get("_aspect"):  # pop overshoot + wobble make an image ~25 % bigger than its width for a moment
        bw, bh = w * 1.25, w * item["_aspect"] * 1.25
    else:  # Montserrat 800 caps + black stroke: ~0.8 of the font size per character (measured 2026-11-18)
        lines = str(item.get("text", "")).split("\n")
        bw, bh = min(1080, max(len(x) for x in lines) * w * 0.8), len(lines) * w * 1.2
    return (cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2)


def place_emojis(stickers, ui_cards, ui_top=560, cap_y=1180, margin=30, badge=None):
    """User rule 2026-11-18: a reaction emoji never covers text, a logo, a card or the caption (the 🤯 sat on the
    "2" of "3X RED DEAD 2"). Each emoji sticker keeps its spot if it is clear, else moves to the nearest free
    spot inside the safe zone (top 210 px, bottom 350 px, button rail x > 930 in the lower half)."""
    W_, H_ = 1080, 1920
    def hit(a, b):
        return not (a[2] + margin <= b[0] or b[2] + margin <= a[0] or a[3] + margin <= b[1] or b[3] + margin <= a[1])
    ui_h = {"bar": 240, "counter": 250}
    moved = []
    for s in stickers:
        if not s.get("_emoji"):
            continue
        t0, t1 = s["at"], s["at"] + s["dur"]
        obst = [(50, cap_y - 80, 1030, cap_y + 80)]  # caption line
        if badge and badge["from"] < t1 and (badge["to"] <= 0 or badge["to"] > t0):
            obst.append((280, badge["top"] - 10, 800, badge["top"] + 80))
        for o in stickers:
            if o is not s and not o.get("_emoji") and o["at"] < t1 and o["at"] + o["dur"] > t0:
                obst.append(_sticker_box(o, o["x"] * W_, o["y"] * H_))
        for c in ui_cards:
            if c["at"] < t1 and c["at"] + c["dur"] > t0:
                h = ui_h.get(c["kind"], 150 + 70 * len(c.get("items") or []))
                obst.append((150, ui_top, 930, ui_top + h))
        def ok(cx, cy):
            b = _sticker_box(s, cx, cy)
            if b[0] < 40 or b[2] > 1040 or b[1] < 210 or b[3] > H_ - 350:
                return False
            if b[3] > H_ / 2 and b[2] > 930:
                return False
            return not any(hit(b, o) for o in obst)
        x0, y0 = s["x"] * W_, s["y"] * H_
        if ok(x0, y0):
            continue
        cands = [(x, y) for x in range(130, 960, 20) for y in range(260, 1560, 20)]
        cands.sort(key=lambda p: (p[0] - x0) ** 2 + (p[1] - y0) ** 2)
        w0, spot = float(s["w"]), None
        for k in (1.0, 0.85, 0.7, 0.55):  # no free spot at full size: try smaller before giving up
            s["w"] = round(w0 * k)
            spot = next((p for p in cands if ok(*p)), None)
            if spot:
                break
        if spot:
            s["x"], s["y"] = round(spot[0] / W_, 3), round(spot[1] / H_, 3)
            moved.append(f"{s.get('_name', 'emoji')} @{s['at']}s -> ({s['x']}, {s['y']}, w {s['w']})")
        else:
            s["dur"] = 0.0  # nowhere clear: dropped below, rather than cover text
            moved.append(f"{s.get('_name', 'emoji')} @{s['at']}s sem lugar livre: removido")
    return moved


def model_overlays(job, props, data):
    """Overlays of the user's model Shorts (references/estilo-modelos-2026-11-18.md): stickers (image from
    assets/stickers/<name>.png or a path, or short text), green arrows, a top badge and the yellow emphasis words of
    the one-word captions. Each item is anchored by "word" (+ "nth") or "at"; every pop gets a pop SFX."""
    caps = props.get("captions") or []
    if data.get("emphasis"):
        props["emphasis"] = [str(w) for w in data["emphasis"]]
    if data.get("captionStyle"):
        props["captionStyle"] = data["captionStyle"]
    pops, warn = [], []
    for key in ("stickers", "arrows"):
        out = []
        for c in data.get(key, []):
            at = c.get("at")
            if at is None and c.get("word"):
                at = _word_at(caps, c["word"], int(c.get("nth", 1)))
                if at is None: warn.append(c["word"])
            if at is None: continue
            base = ({"color": "#FFFFFF", "x": 0.5, "y": 0.5, "w": 420, "rot": 0, "anim": "pop", "shadow": True} if key == "stickers"
                    else {"angle": 90, "len": 230, "color": "#39FF14"})
            item = {**base, **{k: v for k, v in c.items() if k not in ("word", "nth", "at", "src", "sfx")}}
            item.update({"at": max(0.0, float(at)), "dur": float(c.get("dur", 1.6))})
            if key == "stickers" and c.get("src"):
                path = Path(c["src"]) if Path(c["src"]).suffix else STICKERS / f"{c['src']}.png"
                if not path.exists():
                    sys.exit(f"Adesivo não encontrado: {path} (assets/stickers/<nome>.png ou caminho)")
                from PIL import Image
                with Image.open(path) as im:
                    item["_aspect"] = im.height / max(1, im.width)
                item["_emoji"] = path.stem.startswith("emoji_")
                item["_name"] = path.stem
                item["src"] = stage(job, [{"path": path}], "stk")[0]
            out.append(item)
            if c.get("sfx", True) and item.get("anim", "pop") == "pop" and item["at"] > 0.05:
                pops.append(item["at"])
        if out and key == "stickers":
            b = data.get("badge") if isinstance(data.get("badge"), dict) else None
            bt = _word_at(caps, b["word"], int(b.get("nth", 1))) if b and b.get("word") else None
            box = {"from": bt if bt is not None else float(b.get("from", 0)), "top": int(b.get("top", 205)),
                   "to": (bt or 0) + float(b["dur"]) if b.get("dur") else float(b.get("to", 0))} if b else None
            moved = place_emojis(out, props.get("uiCards") or [], badge=box)
            if moved: say("  emoji fora de texto/card: " + ", ".join(moved))
            out = [{k: v for k, v in x.items() if not k.startswith("_")} for x in out if x["dur"] > 0.05]
        if out:
            props[key] = out
            say(f"  {key}: " + ", ".join(f"{(x.get('text') or Path(str(x.get('src', 'seta'))).stem)[:18]} @{x['at']}s" for x in out))
    if data.get("badge"):
        b = data["badge"] if isinstance(data["badge"], dict) else {"text": str(data["badge"])}
        start = _word_at(caps, b["word"], int(b.get("nth", 1))) if b.get("word") else None
        start = float(b.get("from", 0)) if start is None else max(0.0, start)
        end = start + float(b["dur"]) if b.get("dur") else float(b.get("to", 0))
        props["badge"] = {"text": b["text"], "from": start, "to": end, "top": int(b.get("top", 205))}
    if warn: say("  aviso: overlay sem palavra na narração: " + ", ".join(warn))
    if pops:
        pop = stage(job, [{"path": sfx_palette.ensure_palette()["pop"]}], "sfxpop")[0]
        # one pop per moment: two overlays that land together share it
        props["sfx"] = props.get("sfx", []) + [{"src": pop, "at": t, "volume": 0.3} for t in sorted(set(round(t, 1) for t in pops))]


# ---------------------------------------------------------------- flow 1: topic

def flow_topic(a, game, topic=None, context=""):
    topic = topic or a.topic
    slug = slugify(topic)
    job = WORK / f"{game['id']}_{slug}"
    job.mkdir(parents=True, exist_ok=True)
    data, from_llm = load_script(a, game, lambda: llm.write_script(topic, game, a.target_platform, context))
    headlines = data.get("headlines") or [data.get("headline", "")]
    hooks = data.get("hooks") or [data.get("hook", ["", ""])]
    plan = {"flow": "topic", "title": data.get("title", topic), "headline": headlines[0], "hook": list(hooks[0]),
            "script": data["script"],
            # gameplay from someone else's live (kind streamer_live): on-screen credit + recorded permission/risk
            "credit": data.get("credit") or a.credit or "", "permission": data.get("permission") or a.permission or ""}
    ov = read_json(a.overlays, {}) if a.overlays else {}
    plan["own_overlays"] = bool(ov.get("stickers") or ov.get("arrows") or ov.get("badge"))  # model-Short layers

    report, plan = gate_pre(job, plan, game, a.target_platform, "_text")
    if report["status"] == "FLAGGED":
        stop_flagged(report, job)

    dur, captions = narrate(job, plan["script"], game, a)
    if dur < PLATFORMS[a.target_platform]["min"] and from_llm:
        say(f"  narração com {dur:.1f} s, abaixo do mínimo de {a.target_platform}: pedindo roteiro maior")
        data = llm.write_script(topic, game, a.target_platform, context, longer=True)
        plan["script"] = data["script"]
        dur, captions = narrate(job, plan["script"], game, a)

    gap_win = None
    if data.get("voice_gap"):
        dur, captions, gap_win = apply_voice_gap(job, captions, dur, data["voice_gap"])

    ok_any = False
    jobs, finals = [], []
    items = [] if data.get("list_cards") is False else list_items(plan["script"], captions, game)
    if items:
        say("  cards de lista: " + ", ".join(f"{it['index']} {it['label']} @{it['at']}s" + (f" [{it['tag']}]" if it["tag"] else "") for it in items))
    anchors = [(it["at"], it["tag"]) for it in items if it["tag"]]
    # "word_anchors": [["iguana", "natureza"], ...] -> that tag's clip starts on the first time the word is spoken.
    # A 3rd item ([word, tag, follow]) keeps the following filler shots on `follow` until the next section.
    sections = []
    for word, tag, *follow in data.get("word_anchors") or []:
        hit = next((c for c in captions if re.sub(r"\W", "", c["text"].lower()) == word.lower()), None)
        if hit:
            anchors.append((round(hit["startMs"] / 1000 - 0.15, 2), tag))
            if follow:
                sections.append((round(hit["startMs"] / 1000 - 0.15, 2), follow[0]))
        else:
            say(f"  aviso: âncora '{word}' não encontrada na narração")
    # "time_anchors": [["not", 0.6, "*"]] -> a scene change that many seconds after that word's anchor. With a word
    # anchor on the same word it makes a flash insert (2026-11-20: 0.6 s jet-ski-dog teaser in the hook, then back).
    for word, after, tag in data.get("time_anchors") or []:
        hit = next((c for c in captions if re.sub(r"\W", "", c["text"].lower()) == word.lower()), None)
        if hit:
            anchors.append((round(hit["startMs"] / 1000 - 0.15 + float(after), 2), tag))
    ov_ui = (read_json(a.overlays, {}) if a.overlays else {}).get("uiCards") or []
    if ov_ui:
        a.ui_resolved, ui_warn = resolve_ui_cards(ov_ui, captions)
        if ui_warn: say("  aviso: " + "; ".join(ui_warn))
        tagmap = game.get("other_game_tags") or {}
        extra = card_game_anchors(a.ui_resolved, anchors, tagmap)
        if extra:
            anchors += extra
            say("  B-roll do card de outro jogo: " + ", ".join(f"{g}@{t}" for t, g in extra))
    for v in range(max(1, a.variations)):
        card = (read_json(a.overlays, {}) if a.overlays else {}).get("layout") == "card"
        broll = pick_broll(game["id"], data.get("tags"), dur, seed=v, anchors=anchors, fps=a.fps,
                           sources=data.get("sources_only"), opener=data.get("opener"), horizontal_only=card,
                           max_shot=float(data.get("max_shot", 2.5)), sections=sections,
                           tag_order=data.get("tag_order"))
        vplan = {**plan, "headline": headlines[v % len(headlines)], "hook": list(hooks[v % len(hooks)]),
                 "captions": captions, "duration": dur, "broll": broll, "music": music_bed(a, job), "pov": a.pov}
        report, vplan = gate_pre(job, vplan, game, a.target_platform, f"_v{v + 1}")
        if report["status"] == "FLAGGED":
            stop_flagged(report, job)
        refs = stage(job, vplan["broll"], "b")
        props = base_props(a, game, dur, captions, vplan["headline"], vplan["hook"], vplan["credit"], job=job, plan=vplan)
        props["narrationSrc"] = "media:media/narration_gap.wav" if gap_win else "media:media/narration.wav"
        props["broll"] = broll_props(refs, vplan["broll"])
        # Same arithmetic as BrollTrack (each clip lasts round(dur * fps) - 1 frames), so the printed plan,
        # the whoosh cues and any overlay timed from it land on the right shot.
        times, t = [], 0.0
        for c in vplan["broll"]:
            times.append(round(t, 3))
            t += max(2, round(c["dur"] * a.fps) - 1) / a.fps
        props["sfx"] = sfx_cues(job, times, dur)
        if gap_win:  # no whoosh/pop over the silent beat; the gameplay's own sound plays there instead
            props["sfx"] = [c for c in props["sfx"] if not (gap_win[0] - 0.1 <= c["at"] <= gap_win[1])]
            if (data["voice_gap"] or {}).get("game_audio"):
                ga = stage(job, [{"path": job / "media" / "gameaudio.wav"}], "gameaudio")[0]
                props["sfx"].append({"src": ga, "at": round(gap_win[0], 3), "volume": float(data["voice_gap"].get("game_volume", 0.9))})
        props["listCards"] = [{"at": it["at"], "dur": it["dur"], "index": it["index"], "label": it["label"]} for it in items]
        if items:
            pop = stage(job, [{"path": sfx_palette.ensure_palette()["pop"]}], "sfxpop")[0]
            props["sfx"] += [{"src": pop, "at": it["at"], "volume": 0.22} for it in items]
        extras(a, job, props, game)
        jobs.append({"id": f"v{v + 1}", "composition": COMPOSITIONS[a.layout], "props": props, "output": f"render_v{v + 1}.mp4"})
        finals.append((v, report, vplan))
        if a.plan_only:
            say(f"plano v{v + 1} ({dur:.1f} s): entrada | duração | tag | clipe")
            for t0, c in zip(times, vplan["broll"]):
                say(f"  {t0:5.1f} | {c['dur']:.1f} | {c['meta'].get('tag', ''):11} | {Path(c['path']).name}")
    if a.plan_only:
        write_json(job / "jobs_plan.json", jobs)  # for still previews: node render.mjs --still <s>
        say("plan-only: nada renderizado. Escreva --overlays com os tempos acima e rode de novo sem --plan-only.")
        return True
    say(f"render: {len(jobs)} vídeo(s), {dur:.1f} s, {a.fps} fps")
    render(job, jobs)
    import json as _json
    for line in (LOGS / f"render_{job.name}.log").read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith('{"action":"render"') and '"status":"complete"' in line:
            r = _json.loads(line)
            say(f"  render {r['id']}: {r['sec']} s ({r.get('frames')} quadros, {r.get('fps')} quadros/s)")
    for v, report, vplan in finals:
        name = f"{game['id']}_{slug}_v{v + 1}"
        final = finalize(job / f"render_v{v + 1}.mp4", job / f"{name}.mp4")
        meta = {"name": name, "flow": "topic", "topic_slug": slug, "title": vplan["title"],
                "description": data.get("description", ""), "sources": data.get("sources", []),
                "facts": "unverified" if from_llm else "checked_by_director"}
        ok_any |= gate_post(final, report, game, a.target_platform, meta, a.visual)
    return ok_any


# ---------------------------------------------------------------- flow 2: translate

def flow_translate(a, game):
    slug = slugify(a.translate_url.rstrip("/").split("/")[-1][-28:] or "url")
    job = WORK / f"{game['id']}_tr_{slug}"
    src, info = (Path(a.translate_url), {"uploader": "", "title": Path(a.translate_url).stem, "url": ""}) \
        if Path(a.translate_url).exists() else asset_miner.download(a.translate_url, job)
    src_label = LANGS[a.lang]["source"]
    who = info.get("uploader") or a.credit
    credit = f"{src_label}: {who}" if who else ""
    tr = voice_sync.transcribe_file(src, job / "source_transcript.json", prompt=game.get("whisper_prompt"))
    original = " ".join(s["text"] for s in tr["segments"])
    say(f"origem: {info.get('title', '')[:70]} | {tr['language']} | {tr['duration']:.0f} s | transcrição: {job / 'source_transcript.json'}")

    literal = ""

    def make():
        nonlocal literal
        translated = llm.translate_batch(tr["segments"], game)
        literal = " ".join(s["tx"] for s in translated)
        return llm.rewrite_with_commentary(literal, game, info.get("uploader") or a.credit or "the original video", a.target_platform)

    data, tr_from_llm = load_script(a, game, make)
    literal = literal or data.get("literal_translation", "")
    headlines = data.get("headlines") or [data.get("headline", "")]
    hooks = data.get("hooks") or [data.get("hook", ["", ""])]
    dur, captions = narrate(job, data["script"], game, a)

    sinfo = probe(src)
    main_dur = min(dur, sinfo["duration"])
    cut(src, job / "media" / "main.mp4", 0, main_dur, audio=False, height=min(SRC_MAX_H, sinfo["height"]))
    minfo = probe(job / "media" / "main.mp4")
    layout = a.layout
    broll = pick_broll(game["id"], data.get("tags"), dur) if asset_miner.library(game["id"]) else []
    if layout == "split" and not broll:
        layout = "classic"
    plan = {"flow": "translate", "title": data.get("title", info.get("title", "")), "headline": headlines[0],
            "hook": list(hooks[0]), "script": data["script"], "captions": captions, "duration": dur,
            "literal_translation": literal, "credit": credit, "broll": broll if layout == "split" else [],
            "source_text": f"{info.get('title', '')} {original}", "permission": permission_note(a, info),
            "main": {"path": str(job / "media" / "main.mp4"), "kind": "third_party_video", "volume": 0.0}}
    report, plan = gate_pre(job, plan, game, a.target_platform)
    if report["status"] == "FLAGGED":
        stop_flagged(report, job)

    props = base_props(a, game, dur, captions, plan["headline"], plan["hook"], credit)
    props["narrationSrc"] = "media:media/narration.wav"
    w, h = minfo["width"], minfo["height"]
    main = {"src": "media:media/main.mp4", "sourceWidth": w, "sourceHeight": h, "volume": 0.0,
            "loop": main_dur < dur - 0.2, "durationInSeconds": round(main_dur, 3)}
    by, bh = letterbox_rows(job / "media" / "main.mp4")
    target = 9 / 16 if layout == "classic" else 1080 / 960
    if w / bh > target or bh < h:
        cw = min(w, round(bh * target))
        main["crop"] = {"x": (w - cw) // 2, "y": by, "w": cw, "h": bh}
    props["main"] = main
    if layout == "split":
        refs = stage(job, plan["broll"], "b")
        props["broll"] = broll_props(refs, plan["broll"])
    props["sfx"] = sfx_cues(job, [], dur)
    extras(a, job, props, game)
    render(job, [{"id": "v1", "composition": COMPOSITIONS[layout], "props": props, "output": "render_v1.mp4"}])
    name = f"{game['id']}_tr_{slug}"
    final = finalize(job / "render_v1.mp4", job / f"{name}.mp4")
    meta = {"name": name, "flow": "translate", "topic_slug": slugify(info.get("title", slug)), "title": plan["title"],
            "description": data.get("description", ""), "credit": credit, "source_url": info.get("url", ""),
            "sources": data.get("sources", []), "facts": "unverified" if tr_from_llm else "checked_by_director"}
    return gate_post(final, report, game, a.target_platform, meta, a.visual)


def letterbox_rows(video):
    """(y, h) of the picture inside black bars (cinema-format sources). (0, height) when there are none."""
    import collections
    info = probe(video)
    out = run(["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{min(5.0, info['duration'] / 3):.1f}", "-i", video,
               "-vf", "fps=1,cropdetect=24:2:0", "-frames:v", "12", "-f", "null", "-"], check=False).stderr
    found = collections.Counter(re.findall(r"crop=\d+:(\d+):\d+:(\d+)", out)).most_common(1)
    if not found:
        return 0, info["height"]
    h, y = int(found[0][0][0]), int(found[0][0][1])
    return (y, h) if h < info["height"] * 0.97 else (0, info["height"])


def black_seconds(video):
    """Seconds of (near) black picture: title cards such as white text on black, fades."""
    out = run(["ffmpeg", "-hide_banner", "-nostats", "-i", video, "-vf", "blackdetect=d=0.3:pic_th=0.90:pix_th=0.12",
               "-an", "-f", "null", "-"], check=False).stderr
    return round(sum(float(x) for x in re.findall(r"black_duration:([\d.]+)", out)), 2)


def permission_note(a, info):
    """Why we may reuse someone else's video: Creative Commons licence on the source, or the user's own note."""
    lic = (info.get("license") or "")
    if "creative commons" in lic.lower():
        return f"licença do vídeo de origem: {lic}"
    return getattr(a, "permission", "") or ""


def is_publisher(uploader, game):
    """True when the source channel is the game's own publisher (trailers: licensed music in the audio)."""
    names = [n.strip().lower() for n in re.split(r"[/,]", game.get("publisher", "")) if n.strip()]
    return any(n and (n in (uploader or "").lower() or (uploader or "").lower() in n) for n in names) and bool(uploader)


# ---------------------------------------------------------------- flow 3: clips of a long video

def streamer_scenes(captions, dur, seed=1, cam_ok=True):
    """Scene list for StreamerShort, modelled on a viral streamer clip (@gamescortestv, studied 2026-11-18): shots of
    1.5-2.6 s (mean 1.7 s in the reference), cut on a word boundary, cycling cam-over-game, game alone, game-over-cam
    and a short full-cam punch on reactions. Opens on cam-over-game so the face and the game sell the hook together."""
    rng = random.Random(seed)
    starts = sorted({round(c["startMs"] / 1000, 2) for c in captions})
    cycle = ["split", "game", "split", "splitFlip", "game", "split"] + (["cam"] if cam_ok else ["game"])
    out, t, k = [], 0.0, 0
    while t < dur - 0.4:
        lay = "split" if not out else cycle[k % len(cycle)]
        want = 2.4 if not out else (1.1 if lay == "cam" else rng.uniform(1.5, 2.6))
        end = t + want
        near = [x for x in starts if t + 1.0 <= x <= t + want + 0.8]
        if near:
            end = min(near, key=lambda x: abs(x - (t + want)))
        end = min(end, dur)
        if dur - end < 1.0:
            end = dur
        out.append({"at": round(t, 2), "dur": round(end - t, 2), "layout": lay, "zoom": 1.12 if lay == "cam" else 1.0})
        t, k = end, k + 1
    return out

def streamer_fixed_scenes(captions, dur):
    """Default StreamerShort layout since 2026-11-19 (study of 5 clip-channel tutorials + our own sheet: our cut mode
    spent most of the clip on full-screen driving and lost the face). Cam over game for the whole clip; a jump-zoom on
    the face alternates 1.0 / 1.18 at sentence ends at least 2.4 s apart (the podcast-clip 'zoom at end of thought')."""
    ends = [round(c["endMs"] / 1000, 2) for c in captions if re.search(r"[.!?]$", c.get("text", "").strip())]
    out, t, k = [], 0.0, 0
    for e in ends + [dur]:
        if e - t < 2.4 and e < dur:
            continue
        end = dur if dur - e < 1.2 else min(e, dur)
        out.append({"at": round(t, 2), "dur": round(end - t, 2), "layout": "split", "zoom": 1.18 if k % 2 else 1.0})
        t, k = end, k + 1
        if t >= dur - 0.05:
            break
    return out


def streamer_punches(clip, dur, max_n=None):
    """Reaction hits for StreamerShort (2026-11-18): the stream's loudest momentary peaks (yells, crashes), at least
    6 LU over the clip median and 2.5 s apart, get a fast punch-in; the 2 loudest also shake + flash. Never in the
    first 0.3 s (the hook frame stays clean for the thumb) nor the last 0.5 s."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(clip), "-af", "ebur128=framelog=info", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="ignore").stderr
    pts = [(float(t), float(m)) for t, m in re.findall(r"t:\s*([\d.]+)\s+TARGET.*?M:\s*(-?[\d.]+)", r)]
    pts = [(t, m) for t, m in pts if 0.3 <= t <= dur - 0.5 and m > -70]
    if len(pts) < 10:
        return []
    med = sorted(m for _, m in pts)[len(pts) // 2]
    out = []
    for t, m in sorted(pts, key=lambda x: -x[1]):
        if m < med + 6 or len(out) >= (max_n or max(2, int(dur / 6))):
            break
        if all(abs(t - o["at"]) >= 2.5 for o in out):
            out.append({"at": round(t - 0.1, 2), "s": 0.14 if m >= med + 10 else 0.10, "m": m})
    for o in sorted(out, key=lambda x: -x["m"])[:2]:
        o["shake"] = True
    return [{k: v for k, v in o.items() if k != "m"} for o in sorted(out, key=lambda x: x["at"])]

def flow_clip(a, game):
    slug = slugify(a.clip_url.rstrip("/").split("/")[-1][-28:] or "url")
    job = WORK / f"{game['id']}_clip_{slug}"
    src, info = (Path(a.clip_url), {"uploader": "", "title": Path(a.clip_url).stem, "url": ""}) \
        if Path(a.clip_url).exists() else asset_miner.download(a.clip_url, job)
    credit = f"{LANGS[a.lang]['source']}: {info.get('uploader') or a.credit}" if (info.get("uploader") or a.credit) else ""
    tpath = job / "source_transcript.json"
    tr = read_json(tpath) if tpath.exists() else voice_sync.transcribe_file(src, tpath, prompt=game.get("whisper_prompt"))
    say(f"origem: {info.get('title', '')[:70]} | {tr['language']} | {tr['duration']:.0f} s | transcrição: {tpath}")
    if a.transcribe_only:
        say("transcrição pronta. Escolha os cortes (references/scoring-rubric.md + ganchos.md) e passe --segments-file.")
        return True

    own = a.source_kind == "own_gameplay"
    if a.segments_file:
        segs = read_json(a.segments_file)["segments"]
    elif a.highlights or (own and tr.get("word_count", 0) < tr["duration"] * 0.5):
        # Gameplay without (much) speech: propose the loudest, busiest moments instead of scoring a transcript.
        from src import highlights
        segs = [{**h, "hook_line1": "", "hook_line2": "", "title": info.get("title", ""), "scores": {"safety": 100}}
                for h in highlights.find(src, a.clips, a.clip_seconds)]
        say("destaques por áudio: " + ", ".join(f"{h['start']:.0f}-{h['end']:.0f}s" for h in segs))
    else:
        segs = llm.score_segments(tr, game, a.clips)["segments"]
    segs = [s for s in segs if s.get("scores", {}).get("safety", 100) >= 80][: a.clips]
    if not segs:
        sys.exit("Nenhum corte passou no Brand & Platform Safety Score (>= 80).")
    for i, s in enumerate(segs, 1):
        s["id"] = i
    write_json(job / "segments.json", {"segments": segs})
    if any(sg["words"] for sg in tr["segments"]):  # snapping needs spoken words; silent gameplay keeps its windows
        run([sys.executable, SRC / "snap_boundaries.py", "--segments", job / "segments.json", "--transcript", tpath,
             "--input-video", src, "--output", job / "segments_snapped.json"], "snap")
        segs = read_json(job / "segments_snapped.json")["segments"]

    clips_dir = job / "media"
    clips_dir.mkdir(parents=True, exist_ok=True)
    for s in segs:
        cut(src, clips_dir / f"clip_{s['id']:02d}.mp4", s["start"], s["end"] - s["start"], audio=True, height=min(SRC_MAX_H, probe(src)["height"]))
    reframe = {}
    try:
        run([sys.executable, SRC / "compute_reframe.py", "--clips-dir", clips_dir, "--content-type", a.content_type,
             "--output", job / "reframe.json"], "reframe")
        reframe = read_json(job / "reframe.json", {}).get("clips", {})
    except Exception as e:
        say(f"  reframe indisponível ({str(e).splitlines()[0][:80]}): corte central")

    words = [w for s in tr["segments"] for w in s["words"]]
    broll_lib = asset_miner.library(game["id"])
    layout = a.layout if (a.layout == "classic" or broll_lib) else "classic"
    jobs, pending = [], []
    for s in segs:
        clip = clips_dir / f"clip_{s['id']:02d}.mp4"
        cinfo = probe(clip)
        dur, w, h = cinfo["duration"], cinfo["width"], cinfo["height"]
        captions = [{"text": " " + x["word"], "startMs": int((x["start"] - s["start"]) * 1000), "endMs": int((x["end"] - s["start"]) * 1000)}
                    for x in words if x["start"] >= s["start"] - 0.05 and x["end"] <= s["end"] + 0.05]
        captions = [] if a.no_captions else (captions_in_language(tr, s, game, a.lang) or [c for c in captions if c["startMs"] >= 0])
        captions = clean_caps(captions)
        broll = pick_broll(game["id"], None, dur, seed=s["id"]) if layout == "split" else []
        plan = {"flow": "clip", "title": s.get("title", info.get("title", "")), "headline": s.get("headline", ""),
                "hook": [s.get("hook_line1", ""), s.get("hook_line2", "")], "script": " ".join(c["text"].strip() for c in captions),
                "captions": captions, "duration": dur, "credit": "" if own else credit, "broll": broll,
                "music": music_bed(a, job), "pov": a.pov, "permission": permission_note(a, info),
                "no_speech": not captions and (a.no_captions or a.highlights),
                "main": {"path": str(clip), "volume": 1.0, "black_seconds": black_seconds(clip),
                         "kind": "own_gameplay" if own else
                         ("official_trailer_audio" if is_publisher(info.get("uploader"), game) else "commentary_source")}}
        report, plan = gate_pre(job, plan, game, a.target_platform, f"_c{s['id']}")
        if report["status"] == "FLAGGED":
            say(f"corte {s['id']} FLAGGED no pré-flight: {', '.join(report['failed'])} (pulado)")
            continue
        crop = (reframe.get(clip.name) or {}).get("crop")
        target = 9 / 16 if layout == "classic" else 1080 / 960
        cx = crop["x"] + crop["w"] / 2 if crop else w / 2
        by, bh = letterbox_rows(clip)  # cinema-format source: crop inside the black bars
        cw = min(w, round(bh * target))
        main = {"src": f"media:media/{clip.name}", "sourceWidth": w, "sourceHeight": h, "volume": 1.0,
                "crop": {"x": int(min(max(0, cx - cw / 2), w - cw)), "y": by, "w": cw, "h": bh}}
        props = base_props(a, game, dur, captions, plan["headline"], plan["hook"], plan["credit"], job=job, plan=plan)
        props["main"] = main
        if layout == "streamer":
            # Edited stream clip: whole frame as source; the composition cuts webcam and game out of it per scene.
            ov = read_json(a.overlays, {}) if a.overlays else {}
            props["main"] = {"src": main["src"], "sourceWidth": w, "sourceHeight": h, "volume": 1.0}
            props["camBox"] = ov.get("camBox") or {"x": 0, "y": 0, "w": w // 4, "h": h // 4}
            props["gameBox"] = ov.get("gameBox") or {"x": w // 8, "y": 0, "w": w * 3 // 4, "h": h}
            if (ov.get("streamer_mode") or "fixed") == "cuts":  # 11-18 cut mode, only on request
                props["scenes"] = streamer_scenes(captions, dur, seed=s["id"], cam_ok=props["camBox"]["h"] >= 540)  # small stream webcams blur at full 9:16
            else:  # 2026-11-19 default: cam over game the whole clip + title band on the seam from frame 0
                props["scenes"] = streamer_fixed_scenes(captions, dur)
                props["titleBand"], props["headlineFrom"] = True, 0
                band = s.get("band") or re.sub(r"[^\w\s'!?.,:-]", "", s.get("title") or "").strip()
                if band:  # the band narrates the action ("STEALING HATS IN GTA 6!"); falls back to headline/hook
                    props["headline"] = band
            props["punches"] = [] if ov.get("punches") is False else (ov.get("punches") or streamer_punches(clip, dur))
            say(f"corte {s['id']}: {len(props['punches'])} punch-in(s) em " + ", ".join(f"{x['at']:.1f}s" for x in props["punches"]))
            if props.get("musicSrc"):
                props["musicVolume"] = 0.11  # user 2026-11-18: music "a little louder" than 0.08 (+2.8 dB), still under the streamer
            props["sfx"] = []  # no whoosh on camera/scene switches (user, 2026-11-18): the streamer's own audio carries the clip
            if s.get("stickers") or s.get("emphasis"):  # per-cut emoji pops / yellow words from the segments file
                model_overlays(job, props, {"stickers": s.get("stickers") or [], "emphasis": s.get("emphasis") or []})
        if layout == "split":
            refs = stage(job, plan["broll"], "b")
            props["broll"] = broll_props(refs, plan["broll"])
        if layout != "streamer":
            props["sfx"] = sfx_cues(job, [], dur)[:1]
        jobs.append({"id": f"c{s['id']}", "composition": COMPOSITIONS[layout], "props": props, "output": f"render_c{s['id']}.mp4"})
        pending.append((s, report, plan))
    if not jobs:
        sys.exit(2)
    say(f"render: {len(jobs)} corte(s), layout {layout}")
    render(job, jobs)
    ok_any = False
    for s, report, plan in pending:
        name = f"{game['id']}_clip_{slug}_{s['id']:02d}"
        final = finalize(job / f"render_c{s['id']}.mp4", job / f"{name}.mp4")
        meta = {"name": name, "flow": "clip", "topic_slug": slugify(plan["title"]), "title": plan["title"],
                "description": s.get("why", ""), "credit": credit, "source_url": info.get("url", ""),
                "permission": plan.get("permission", "")}
        ok_any |= gate_post(final, report, game, a.target_platform, meta, a.visual)
    return ok_any


# ---------------------------------------------------------------- flow 5: side-by-side comparison

def _clip_spec(spec):
    """'FILE@12-30' -> (path, start, dur). Without '@', the first 30 s."""
    path, _, rng = spec.partition("@")
    start, _, end = rng.partition("-")
    start = float(start or 0)
    return Path(path), start, (float(end) - start) if end else 30.0


def flow_compare(a, game):
    """Two clips stacked with a label each ("GTA 5" / "GTA 6"). Narration from --script-file, or the game audio
    of an own-gameplay clip. Trailer audio is never kept (licensed music)."""
    labels = [x.strip() for x in a.labels.split("|")] + ["", ""]
    kinds = (a.compare_kinds.split(",") + ["own_gameplay", "own_gameplay"])[:2]
    slug = slugify("-vs-".join(labels[:2]) or "compare")
    job = WORK / f"{game['id']}_cmp_{slug}"
    (job / "media").mkdir(parents=True, exist_ok=True)
    data = read_json(a.script_file) if a.script_file else {}
    captions, dur = [], None
    if data.get("script"):
        dur, captions = narrate(job, data["script"], game, a)
    mains, plan_clips = [], []
    for i, spec in enumerate(a.compare):
        src, start, length = _clip_spec(spec)
        if not src.exists():
            sys.exit(f"Arquivo não encontrado: {src}")
        length = min(length, probe(src)["duration"] - start)
        keep_audio = kinds[i] == "own_gameplay" and not data.get("script") and a.compare_audio == ("top", "bottom")[i]
        dst = job / "media" / f"cmp_{i}.mp4"
        cut(src, dst, start, length, audio=keep_audio, height=min(1440, probe(src)["height"]))
        info = probe(dst)
        by, bh = letterbox_rows(dst)
        cw = min(info["width"], round(bh * 1080 / 960))
        mains.append({"src": f"media:media/{dst.name}", "sourceWidth": info["width"], "sourceHeight": info["height"],
                      "volume": 1.0 if keep_audio else 0.0, "loop": True, "durationInSeconds": round(info["duration"], 3),
                      "crop": {"x": (info["width"] - cw) // 2, "y": by, "w": cw, "h": bh}})
        plan_clips.append({"path": str(dst), "dur": 4.0, "meta": {"kind": kinds[i], "tag": (game.get("safe_opening_tags") or [""])[0]}})
    dur = dur or min(m["durationInSeconds"] for m in mains)
    if not data.get("script") and not any(m["volume"] for m in mains):
        sys.exit("Comparação sem som: passe --script-file com narração ou use um clipe own_gameplay com --compare-audio top|bottom.")
    headlines = data.get("headlines") or [a.headline or " VS ".join(l for l in labels[:2] if l)]
    hooks = data.get("hooks") or [["", ""]]
    plan = {"flow": "compare", "title": data.get("title", headlines[0]), "headline": headlines[0], "hook": list(hooks[0]),
            "script": data.get("script", ""), "captions": captions, "duration": dur, "broll": plan_clips,
            "music": music_bed(a, job), "pov": a.pov, "main": {"path": str(job / "media" / "cmp_0.mp4"), "kind": "own_gameplay", "volume": 0},
            "credit": a.credit or data.get("credit", ""), "permission": a.permission or data.get("permission", ""),
            "compare_kinds": kinds}
    report, plan = gate_pre(job, plan, game, a.target_platform)
    if report["status"] == "FLAGGED":
        stop_flagged(report, job)
    props = base_props(a, game, dur, captions, plan["headline"], plan["hook"], plan["credit"], job=job, plan=plan)
    props.update(main=mains[0], main2=mains[1], compareLabels=labels[:2])
    if data.get("script"):
        props["narrationSrc"] = "media:media/narration.wav"
    props["sfx"] = sfx_cues(job, [], dur)[:1]
    render(job, [{"id": "v1", "composition": COMPOSITIONS["split"], "props": props, "output": "render_v1.mp4"}])
    name = f"{game['id']}_cmp_{slug}"
    final = finalize(job / "render_v1.mp4", job / f"{name}.mp4")
    meta = {"name": name, "flow": "compare", "topic_slug": slug, "title": plan["title"],
            "description": data.get("description", ""), "sources": data.get("sources", []),
            "credit": plan["credit"], "source_url": data.get("source_url", "")}
    return gate_post(final, report, game, a.target_platform, meta, a.visual)


def flow_compare_pairs(a, game):
    """Silent comparison of the model Shorts (references/estilo-modelos-2026-11-18.md, formato A): the same action in
    two games, in ~2 s pairs. work/<slug>_compare.json:
      {"title": "...", "mode": "split" | "alternate",
       "pairs": [{"a": "assets/footage/gta5/x.mp4[@s-e]", "b": "assets/footage/gta6/y.mp4[@s-e]", "dur": 2.0}, ...],
       "labels": ["2013", "2026"],            # year (or name) of a and b; split: top-right of each half
       "logos": ["gta5", "gta6"],             # assets/stickers/<name>.png; split: top-left of each half,
                                              # alternate: top-centre, switching with the clip
       "description": "...", "sources": [...]}
    split = a on top, b below, both moving together; alternate = a, b, a, b full screen.
    No narration and no captions: --music (own/generated/CC0) is required; game audio is never kept.
    Ends on the last pair with no outro, so the Short loops."""
    data = read_json(a.compare_file)
    mode = data.get("mode", "split")
    slug = slugify(data.get("title") or "compare")
    job = WORK / f"{game['id']}_cmp_{slug}"
    (job / "media").mkdir(parents=True, exist_ok=True)
    items, clips, t = [], [], 0.0
    for i, pr in enumerate(data["pairs"]):
        d = float(pr.get("dur", 2.0))
        for side in ("a", "b"):
            spec = str(pr[side])
            src_s, _, rng = spec.partition("@")
            src = (ROOT / src_s) if not Path(src_s).is_absolute() else Path(src_s)
            if not src.exists():
                sys.exit(f"Clipe não encontrado: {src}")
            meta = read_json(src.with_suffix(".json"), {}) or {}
            start = float(rng.split("-")[0]) if rng else 0.0
            length = min(d, probe(src)["duration"] - start)
            if length < d - 0.05:
                sys.exit(f"Clipe curto demais para {d}s: {spec} ({length:.2f}s)")
            dst = job / "media" / f"pair{i}_{side}.mp4"
            cut(src, dst, start, d, audio=False, height=min(SRC_MAX_H, probe(src)["height"]))
            clips.append({"path": str(dst), "dur": d, "meta": {**meta, "hide_bottom": meta.get("hide_bottom", 0)}})
        t += d
    dur = round(t if mode == "split" else 2 * t, 3)
    music = music_bed(a, job)
    if not music:
        sys.exit("Comparação muda precisa de trilha: --music assets/music/gen_<slug>.wav (própria, gerada ou CC0).")
    plan = {"flow": "compare", "title": data.get("title", slug), "headline": "", "hook": ["", ""], "script": "",
            "captions": [], "duration": dur, "broll": clips, "music": music, "pov": "",
            "credit": a.credit or data.get("credit", ""), "permission": a.permission or data.get("permission", ""),
            "compare_kinds": sorted({c["meta"].get("kind", "") for c in clips}),
            "pairs_locked": True, "no_speech": True, "own_overlays": bool(data.get("labels") or data.get("logos"))}
    if a.target_platform == "shorts":
        a.target_platform = "shorts_loop"
    report, plan = gate_pre(job, plan, game, a.target_platform)
    if report["status"] == "FLAGGED":
        stop_flagged(report, job)
    refs = stage(job, clips, "cmp")
    props = base_props(a, game, dur, [], "", ["", ""], plan["credit"], job=job, plan=plan)
    props["broll"] = broll_props(refs, clips)          # a1, b1, a2, b2 ... (split: even = top, odd = bottom)
    props["showProgressBar"] = False                   # a bar gives the loop away
    props["musicVolume"] = 0.9
    labels, logos = (data.get("labels") or ["", ""]) + ["", ""], (data.get("logos") or []) + ["", ""]
    st = []
    if mode == "split":
        for k, y in ((0, 0.0), (1, 0.5)):
            if logos[k]: st.append({"src": logos[k], "at": 0, "dur": dur, "x": 0.16, "y": y + 0.075, "w": 250, "anim": "none"})
            if labels[k]: st.append({"text": labels[k], "at": 0, "dur": dur, "x": 0.83, "y": y + 0.065, "w": 76,
                                     "color": "#B6FF3B", "anim": "none"})
    else:
        tt = 0.0
        for c in clips:
            k = 0 if len([x for x in st if x.get("_s")]) % 2 == 0 else 1
            if logos[k]: st.append({"src": logos[k], "at": round(tt, 3), "dur": c["dur"], "x": 0.5, "y": 0.155, "w": 330, "anim": "none", "_s": 1})
            else: st.append({"text": labels[k], "at": round(tt, 3), "dur": c["dur"], "x": 0.5, "y": 0.16, "w": 90, "color": "#B6FF3B", "anim": "none", "_s": 1})
            tt += c["dur"]
        for x in st: x.pop("_s", None)
    ov = read_json(a.overlays, {}) if a.overlays else {}
    extras(a, job, props, game)
    model_overlays(job, props, {"stickers": st + (ov.get("stickers") or []), "arrows": ov.get("arrows") or []})
    props["sfx"] = sfx_cues(job, [], dur)[:1]          # strong attack on frame 0 (lesson 2026-11-18), nothing else
    comp = COMPOSITIONS["split"] if mode == "split" else COMPOSITIONS["classic"]
    render(job, [{"id": "v1", "composition": comp, "props": props, "output": "render_v1.mp4"}])
    name = f"{game['id']}_cmp_{slug}"
    final = finalize(job / "render_v1.mp4", job / f"{name}.mp4")
    meta = {"name": name, "flow": "compare", "topic_slug": slug, "title": plan["title"],
            "description": data.get("description", ""), "sources": data.get("sources", []),
            "credit": plan["credit"], "source_url": data.get("source_url", "")}
    return gate_post(final, report, game, a.target_platform, meta, a.visual)


# ---------------------------------------------------------------- flow 4: auto hype

def flow_auto_hype(a, game):
    from src import hype_radar
    topics, dropped = hype_radar.scan(game, max(a.auto_hype * 3, 10))
    write_json(WORK / f"hype_{game['id']}.json", {"game": game["name"], "topics": topics, "dropped": dropped})
    say(f"radar: {len(topics)} temas, descartados {dropped}")
    done = 0
    for tried, t in enumerate(topics):
        if done >= a.auto_hype:
            break
        if tried >= a.auto_hype + 2:  # do not burn hours walking down the list when videos keep failing
            say(f"auto-hype: parei depois de {tried} pautas tentadas")
            break
        say(f"--- {t['title'][:90]}")
        try:
            done += bool(flow_topic(a, game, topic=t["title"], context=f"{t['title']}\n{t['text']}\nFonte: {t['url']}"))
        except llm.NoBrain as e:
            say(f"{e}\nTemas salvos em {WORK / ('hype_' + game['id'] + '.json')}: escreva os roteiros e rode --topic com --script-file.")
            return False
        except SystemExit as e:
            say(f"  tema pulado ({e.code})")
    say(f"auto-hype: {done}/{a.auto_hype} vídeo(s) aprovados em {OUTPUT}")
    return done > 0


def flow_audit(a, game):
    final = Path(a.audit)
    cert_path = final.with_name(final.stem + "_compliance.json")
    old = read_json(cert_path, {}) or {}
    pre = old.get("pre_flight") or {"stage": "pre", "status": "APPROVED", "note": "auditoria avulsa: sem plano de pré-flight"}
    visual = {"verdict": a.visual, "notes": a.visual_notes, "by": "claude-code"} if a.visual in ("pass", "fail") else None
    post = guard.postrender(final, game, a.target_platform, visual=visual)
    # Gemini Vision removido (2026-11-21): veredito visual = Claude Code (folha de QA 4 fps) + auditoria externa do usuário.
    meta = old.get("meta") or {"name": final.stem, "title": final.stem, "flow": "audit"}
    if a.facts == "ok":
        meta["facts"] = "checked_by_director"
    cert = guard.certificate(cert_path, pre, post, {"title": meta.get("title", ""), "flow": meta.get("flow", ""),
                                                    "game": game["id"], "meta": meta})
    return release(final, cert_path, cert, game, meta)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--topic")
    ap.add_argument("--translate-url")
    ap.add_argument("--clip-url")
    ap.add_argument("--auto-hype", type=int)
    ap.add_argument("--audit")
    ap.add_argument("--game", default="gta6")
    ap.add_argument("--target-platform", default="shorts", choices=list(PLATFORMS))
    ap.add_argument("--layout", default="classic", choices=list(COMPOSITIONS))
    ap.add_argument("--variations", type=int, default=1, help="A/B versions: other headline, hook and B-roll order")
    ap.add_argument("--clips", type=int, default=3)
    ap.add_argument("--script-file", help="JSON written by the director (see references/formatos-json.md)")
    ap.add_argument("--segments-file", help="JSON with the chosen cuts")
    ap.add_argument("--overlays", help="JSON with cards / zoomTargets")
    ap.add_argument("--plan-only", action="store_true", help="topic flow: narration, pre-flight and clip timeline; no render")
    ap.add_argument("--transcribe-only", action="store_true", help="clip flow: download + transcript, then stop")
    ap.add_argument("--content-type", default="talking-head", choices=["talking-head", "screen", "podcast"])
    ap.add_argument("--credit", default="", help="source channel name when it cannot be read from the URL")
    ap.add_argument("--lang", default=DEFAULT_LANG, choices=list(LANGS), help="language of the video (default: en-US)")
    ap.add_argument("--voice", default="clone", help="clone (default since 2026-11-16) | edge = fast Edge voice of the language (en: Andrew +8%%) | brian | ava | antonio | clone (cloned channel voice of the language in VoxCPM2, 9-16 min)")
    ap.add_argument("--tts", default="edge", choices=["edge", "voicestudio"])
    ap.add_argument("--rate", default=None, help="Edge speaking rate, default per language (en +8%%)")
    ap.add_argument("--fps", type=int, default=60, choices=[30, 60])   # lei do usuário 2026-11-22: final a 60 fps (rascunho CE_DRAFT=1 força 30)
    ap.add_argument("--caption-style", default="word", choices=["word", "bold", "bounce", "clean"],
                    help="word (default since 2026-11-18, model Shorts) = 1 word mid-frame; bold = 2-3 word pages")
    ap.add_argument("--visual", default="auto", help="manual (padrão efetivo; Gemini Vision removido) | pass | fail (with --audit)")
    ap.add_argument("--visual-notes", default="")
    ap.add_argument("--facts", choices=["ok"], help="with --audit: you checked every fact of an LLM-written script")
    ap.add_argument("--source-kind", default="creator", choices=["creator", "own_gameplay"],
                    help="clip flow: whose video it is. own_gameplay = your recording (no credit, captions optional)")
    ap.add_argument("--permission", default="", help="third-party video: where/how its owner allowed reuse (kept in the certificate)")
    ap.add_argument("--highlights", action="store_true", help="clip flow: pick the loudest moments instead of scoring speech")
    ap.add_argument("--clip-seconds", type=float, default=22.0, help="length of each highlight clip")
    ap.add_argument("--no-captions", action="store_true", help="clip flow: no word captions (raw gameplay moment)")
    ap.add_argument("--pov", default="", help='big text that stays on screen, e.g. "POV: your first hour in GTA 6"')
    ap.add_argument("--music", default="auto", help="off | auto (assets/music) | FILE; needs a .json with kind own|generated|cc0|licensed (generated = ACE-Step local)")
    ap.add_argument("--compare", nargs=2, metavar=("TOP@s-e", "BOTTOM@s-e"), help="side-by-side comparison of two local clips")
    ap.add_argument("--compare-file", help="silent comparison of the model Shorts: JSON with pairs (see flow_compare_pairs)")
    ap.add_argument("--labels", default="", help='comparison labels: "GTA 5|GTA 6"')
    ap.add_argument("--compare-kinds", default="own_gameplay,own_gameplay", help="origin of each clip: own_gameplay|official_trailer|licensed")
    ap.add_argument("--compare-audio", default="bottom", choices=["top", "bottom"], help="which own-gameplay clip keeps its sound")
    ap.add_argument("--headline", default="")
    ap.add_argument("--length", default="standard", choices=["quick", "standard"],
                    help="script length for shorts: quick = 20-32 s (median of the top GTA shorts is 33 s), standard = 40-55 s")
    ap.add_argument("--demo-footage", action="store_true", help="create synthetic test clips for a smoke test")
    a = ap.parse_args()
    if os.environ.get("CE_DRAFT") == "1":
        a.fps = 30  # draft/preview: 540x960 @ 30 (render scale 0.5 is set in render step)
    load_env()
    if a.length == "quick":
        PLATFORMS["shorts"]["script_seconds"] = (20, 32)
    heavy = any([a.topic, a.translate_url, a.clip_url, a.auto_hype, a.compare, a.compare_file]) and not a.plan_only and not a.transcribe_only
    if heavy:
        run_lock()
        if not nvenc_ok():
            say("aviso: NVENC indisponível (driver NVIDIA abaixo de 610 para este ffmpeg): export pelo processador")
    game = localize(load_game(a.game), a.lang)
    llm.LANG = a.lang
    from src import voicestudio_client as _vs
    _vs.VOCAB = game.get("whisper_prompt", "")
    if a.demo_footage:
        demo_footage(a.game)
    try:
        if a.audit:
            ok = flow_audit(a, game)
        elif a.compare_file:
            ok = flow_compare_pairs(a, game)
        elif a.compare:
            ok = flow_compare(a, game)
        elif a.topic:
            ok = flow_topic(a, game)
        elif a.translate_url:
            ok = flow_translate(a, game)
        elif a.clip_url:
            ok = flow_clip(a, game)
        elif a.auto_hype:
            ok = flow_auto_hype(a, game)
        elif a.demo_footage:
            ok = True
        else:
            ap.error("choose one: --topic, --translate-url, --clip-url, --compare, --auto-hype, --audit")
    except llm.NoBrain as e:
        sys.exit(str(e))
    sys.exit(0 if ok else 3)


if __name__ == "__main__":
    main()
