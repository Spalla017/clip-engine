"""Two-stage compliance gate. Rules and sources: references/compliance-rules.md.

  PRE  (before render): leak provenance and wording, first 7 seconds, Content ID, originality, duration.
  POST (after render):  technical probe (audio, duration, metadata, black frames) + 6-frame contact sheet
                        for a visual verdict by Claude Code or Gemini Vision.

A video is APPROVED only when pre passed, the technical probe passed AND the visual verdict is "pass".
Without a visual verdict the certificate says PENDING_VISUAL and the file is not released to output/.

CLI:
  python -m src.compliance_guard --stage pre  --plan work/<slug>/plan.json --game gta6 --platform shorts
  python -m src.compliance_guard --stage post --video FILE.mp4 --game gta6 --platform tiktok
         [--visual pass|fail --visual-notes "..."] [--cert output/NAME_compliance.json]
"""
import argparse
import difflib
import re
import sys
import time
from pathlib import Path

from .common import PLATFORMS, ffmpeg, load_game, probe, read_json, run, say, write_json

FIRST_SECONDS = 7.0

# Heavy profanity (PT/EN). Matched as whole words.
PROFANITY = ["porra", "caralho", "foda", "fodase", "foda-se", "puta", "merda", "buceta", "cu", "viado",
             "arrombado", "fuck", "fucking", "shit", "bitch", "motherfucker", "nigga", "cunt"]
# Graphic violence wording that costs the green icon when it lands in the opening seconds.
GRAPHIC = ["sangue", "sangrando", "sangrento", "decapit", "tortur", "massacre", "chacina", "estupr", "mutila",
           "esquartej", "execução", "executa", "tripas", "gore", "blood", "behead", "slaughter", "rape"]
# Platform watermarks: a re-upload of someone else's export is reused content.
NOT_OUR_TAGS = {"com.apple.quicktime", "handler_name"}
CLEAN_TAGS = {"major_brand", "minor_version", "compatible_brands", "encoder"}


def _norm(text):
    return re.sub(r"\s+", " ", (text or "").lower())


def _has(text, stems):
    text = _norm(text)
    return sorted({s for s in stems if re.search(r"(?<![a-zà-ú])" + re.escape(s), text)})


def _whole(text, words):
    text = _norm(text)
    return sorted({w for w in words if re.search(r"(?<![a-zà-ú])" + re.escape(w) + r"(?![a-zà-ú])", text)})


def _first_words(plan):
    """Text spoken in the first 7 s: from captions when we have them, else the first ~20 words."""
    caps = plan.get("captions") or []
    if caps:
        return " ".join(c["text"].strip() for c in caps if c["startMs"] < FIRST_SECONDS * 1000)
    return " ".join((plan.get("script") or "").split()[:20])


def preflight(plan, game, platform="shorts"):
    """Check the plan, fix what can be fixed honestly, return (report, plan). Never renders."""
    checks, fixes = [], []

    def add(rule, ok, detail=""):
        checks.append({"rule": rule, "ok": bool(ok), "detail": detail})

    # 1. Publisher policy: no footage from leaks or stolen builds; every clip needs known provenance.
    allowed = set(game.get("allowed_footage", []))
    bad = []
    for clip in plan.get("broll", []):
        kind = (clip.get("meta") or {}).get("kind", "unknown")
        if kind not in allowed:
            bad.append(f"{Path(clip['path']).name} ({kind})")
    add("footage_provenance", not bad,
        "Sem origem aprovada: " + ", ".join(bad[:6]) + ". Use trailer/imagem oficial ou gameplay própria "
        "(asset_miner grava o arquivo .json de origem)." if bad else "")

    # 2. Leak wording. Headline, title and hook: swap clickbait for an honest label (the footage rule
    #    above already guarantees nothing leaked is on screen). Script body: needs a human/LLM rewrite.
    leak = game.get("leak_terms", [])
    labels = game.get("honest_labels", ["ANÁLISE"])
    for field in ("headline", "title"):
        if _has(plan.get(field, ""), leak):
            fixes.append(f"{field}: '{plan[field]}' -> '{labels[0]}'")
            plan[field] = labels[0] if field == "headline" else f"{game['name']}: {labels[0].title()}"
    hook = plan.get("hook") or ["", ""]
    for i, line in enumerate(hook):
        if _has(line, leak):
            fixes.append(f"hook[{i}]: '{line}' -> '{labels[min(i + 1, len(labels) - 1)]}'")
            hook[i] = labels[min(i + 1, len(labels) - 1)]
    plan["hook"] = hook
    in_script = _has(plan.get("script", ""), leak)
    add("leak_wording", not in_script,
        f"O roteiro fala de vazamento ({', '.join(in_script)}). Reescreva com base em material oficial "
        "ou trate como rumor sem mostrar/descrever o material vazado; ou troque de tema." if in_script else "")

    src_leak = _has(plan.get("source_text", ""), leak)
    if "source_text" in plan:
        add("source_not_leak", not src_leak,
            f"O vídeo de origem fala de material vazado ({', '.join(src_leak)}): não reaproveite." if src_leak else "")

    # 3. First 7 seconds: no heavy profanity, no graphic wording, calm opening clip.
    opening = _first_words(plan)
    prof, graphic = _whole(opening, PROFANITY), _has(opening, GRAPHIC)
    add("first_7s_text", not (prof or graphic),
        f"Nos primeiros 7 s: {', '.join(prof + graphic)}. Reescreva a abertura." if prof or graphic else "")
    prof_all = _whole(plan.get("script", ""), PROFANITY)
    add("profanity", not prof_all or plan.get("flow") == "clip",
        f"Palavrão no roteiro: {', '.join(prof_all)}" if prof_all else "")
    broll = plan.get("broll", [])
    safe_tags = set(game.get("safe_opening_tags", []))
    if broll and safe_tags and not plan.get("pairs_locked"):  # also with a main clip: in the split layout gameplay is on screen from frame 0
        first_tag = (broll[0].get("meta") or {}).get("tag", "")
        if first_tag not in safe_tags:
            swap = next((i for i, c in enumerate(broll) if (c.get("meta") or {}).get("tag") in safe_tags), None)
            if swap is not None:
                d0, ds = broll[0].get("dur"), broll[swap].get("dur")
                broll[0], broll[swap] = broll[swap], broll[0]
                # keep each slot's length: the timeline (word anchors, cuts) is built on slot durations
                for c, d in ((broll[0], d0), (broll[swap], ds)):
                    src = float((c.get("meta") or {}).get("dur") or d or 0)
                    c["dur"] = min(d, src) if d and src else c.get("dur")
                fixes.append(f"scene_01: clipe '{first_tag}' trocado por '{broll[0]['meta']['tag']}' (abertura calma)")
            add("first_7s_clip", swap is not None,
                "" if swap is not None else f"Clipe de abertura com tag '{first_tag}' e nenhum clipe de {sorted(safe_tags)} disponível.")
        else:
            add("first_7s_clip", True)

    # 4. Content ID: trailer music is licensed. Every game clip must be silent; trailer mains at volume 0.
    loud = [Path(c["path"]).name for c in broll if Path(c["path"]).exists() and probe(c["path"])["has_audio"]]
    add("broll_silent", not loud, "Clipes com áudio (rode asset_miner, que usa -an): " + ", ".join(loud[:6]) if loud else "")
    main = plan.get("main")
    if main and main.get("kind") in ("official_trailer", "third_party_video") and main.get("volume", 1) != 0:
        main["volume"] = 0.0
        fixes.append("main.volume -> 0.0 (áudio original removido)")

    if main and main.get("kind") == "official_trailer_audio":
        add("clip_source_music", False,
            "A origem é um trailer oficial: o áudio tem trilha licenciada (Content ID). Trailer só entra como "
            "B-roll mudo com narração própria (/tema), não como corte com o áudio original.")
    if main and main.get("black_seconds", 0) > 0.6:
        add("title_card_in_cut", False,
            f"{main['black_seconds']} s de tela preta (cartela de título, fade ou cena escura) dentro do corte. Mova o início ou o fim para fora desse trecho.")

    # 5. Originality / reused content: credit, own script, cuts every 3-5 s, own graphics.
    if plan.get("flow") in ("translate", "clip"):
        kind = (plan.get("main") or {}).get("kind")
        if kind in ("commentary_source", "third_party_video"):
            # Someone else's video: reuse is allowed only with a Creative Commons licence on the source or the
            # owner's permission. Credit (in the description, never on screen since 2026-11-22) is required either way and does not replace permission.
            add("creator_permission", bool(plan.get("permission")),
                "Vídeo de outro criador sem autorização registrada. Use fonte com licença Creative Commons "
                "(detectada sozinha) ou passe --permission \"onde/como o dono autorizou\"." if not plan.get("permission") else plan["permission"])
        own = kind == "own_gameplay"  # your own recording needs no source credit
        add("source_credit", own or bool(plan.get("credit")), "Falta o crédito da fonte (vai na descrição, não na tela)." if not (own or plan.get("credit")) else "")
    # third_party_video B-roll (2026-11-19, easter-egg finds from YouTube guides): same rule as live gameplay,
    # the user's recorded risk in "permission"; the channels are listed in the description.
    live = sorted({(c.get("meta") or {}).get("uploader", "") for c in broll
                   if (c.get("meta") or {}).get("kind") in ("streamer_live", "third_party_video")})
    if live:  # gameplay cut from a streamer's live (user's registered exception, 2026-11-18): recorded risk.
        # No on-screen credit by the user's decision (2026-11-18): "não precisa dar crédito".
        add("creator_permission", bool(plan.get("permission")),
            "Gameplay de live sem autorização/risco registrado: \"permission\" no roteiro." if not plan.get("permission") else plan["permission"])
    if plan.get("flow") == "compare":
        # Footage that is neither ours nor the publisher's (CC/stock/creator) needs licence + on-screen credit.
        third = [k for k in plan.get("compare_kinds", []) if k not in ("own_gameplay", "official_trailer", "official_screenshot")]
        if third:
            add("source_credit", bool(plan.get("credit")), "Clipe licenciado/de terceiro sem crédito para a descrição (--credit)." if not plan.get("credit") else plan["credit"])
            add("creator_permission", bool(plan.get("permission")),
                "Clipe de terceiro sem licença registrada: passe --permission \"licença e link\"." if not plan.get("permission") else plan["permission"])
    if plan.get("flow") == "translate":
        ratio = difflib.SequenceMatcher(None, _norm(plan.get("literal_translation", "")), _norm(plan.get("script", ""))).ratio()
        add("transformative_script", ratio < 0.6,
            f"Roteiro {ratio:.0%} igual à tradução literal: é dublagem, não análise. Reescreva com comentário próprio." if ratio >= 0.6 else f"semelhança {ratio:.0%}")
    if broll and not main:
        slow = [c for c in broll if c.get("dur", 0) > 5.5]
        add("visual_pacing", not slow, f"{len(slow)} clipe(s) de B-roll com mais de 5,5 s." if slow else "")
    own_clip = (plan.get("main") or {}).get("kind") == "own_gameplay"
    # own overlays (stickers, year labels, arrows) count as own layers in the silent comparison of the model Shorts
    layers = bool(plan.get("headline") or any(hook) or plan.get("pov") or plan.get("own_overlays"))
    add("own_graphics", layers and (own_clip or plan.get("no_speech") or bool(plan.get("captions") or plan.get("script"))),
        "Faltam camadas próprias (gancho, manchete ou texto POV; em vídeo de terceiros, também legenda).")
    music = plan.get("music")
    if music:
        add("music_license", music.get("kind") in ("own", "generated", "cc0", "licensed"),
            f"Trilha sem licença registrada ({music.get('path')}): crie o .json ao lado com kind own|generated|cc0|licensed e a fonte.")

    # 6. Duration for the target platform (TikTok Creator Rewards: longer than 1 min).
    dur = plan.get("duration")
    if dur:
        lim = PLATFORMS[platform]
        add("duration", lim["min"] <= dur <= lim["max"], f"{dur:.1f} s fora de {lim['min']}-{lim['max']} s para {platform}.")

    failed = [c for c in checks if not c["ok"]]
    report = {"stage": "pre", "status": "FLAGGED" if failed else "APPROVED", "platform": platform,
              "checks": checks, "auto_fixes": fixes, "failed": [c["rule"] for c in failed]}
    return report, plan


def _detect(video, flt, pattern, audio=False):
    out = run(["ffmpeg", "-hide_banner", "-nostats", "-i", video, "-af" if audio else "-vf", flt,
               "-vn" if audio else "-an", "-f", "null", "-"], check=False).stderr
    return re.findall(pattern, out)


def contact_sheet(video, out_dir, duration):
    """Six frames (0.5 s, 3.0 s, 6.5 s, 25 %, 50 %, 90 %) in one 960 px sheet."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamps = [0.5, 3.0, 6.5, duration * 0.25, duration * 0.5, duration * 0.9]
    stamps = [min(max(0.0, t), max(0.0, duration - 0.1)) for t in stamps]
    for i, t in enumerate(stamps):
        ffmpeg(["-ss", f"{t:.2f}", "-i", video, "-frames:v", "1", "-vf", "scale=320:-2", "-q:v", "3",
                out_dir / f"qa_{i}.jpg"], "qa")
    sheet = out_dir / f"{Path(video).stem}_qa.jpg"
    ffmpeg(["-framerate", "1", "-i", out_dir / "qa_%d.jpg", "-vf", "tile=3x2", "-frames:v", "1", "-q:v", "3", sheet], "qa")
    for i in range(6):
        (out_dir / f"qa_{i}.jpg").unlink(missing_ok=True)
    return sheet, [round(t, 2) for t in stamps]


def postrender(video, game, platform="shorts", sheet_dir=None, visual=None):
    """Probe the finished mp4. visual: None | {"verdict": "pass"|"fail", "notes": str, "by": str}."""
    video = Path(video)
    info = probe(video)
    checks = []

    def add(rule, ok, detail=""):
        checks.append({"rule": rule, "ok": bool(ok), "detail": detail})

    lim = PLATFORMS[platform]
    # User law 2026-11-22: final is always 1440x2560 @ 60 fps. Drafts (540x960 @ 30, CE_DRAFT=1) stay FLAGGED on purpose.
    add("format_1440x2560", (info["width"], info["height"]) == (1440, 2560), f"{info['width']}x{info['height']}")
    add("fps_60", round(info.get("fps") or 0) == 60, f"{info.get('fps')} fps")
    add("duration", lim["min"] <= info["duration"] <= lim["max"],
        f"{info['duration']:.2f} s (alvo {platform}: {lim['min']}-{lim['max']} s)")
    add("has_audio", info["has_audio"], "" if info["has_audio"] else "Sem faixa de áudio.")
    if info["has_audio"]:
        vol = _detect(video, "volumedetect", r"(mean|max)_volume: (-?[\d.]+) dB", audio=True)
        levels = {k: float(v) for k, v in vol}
        add("audio_not_silent", levels.get("mean", -91) > -35, f"média {levels.get('mean')} dB")
        add("audio_not_clipping", levels.get("max", 0) <= -0.5, f"pico {levels.get('max')} dB")
        gaps = _detect(video, "silencedetect=noise=-50dB:d=2", r"silence_duration: ([\d.]+)", audio=True)
        add("no_dead_air", not gaps, f"{len(gaps)} trecho(s) mudo(s) de 2 s ou mais" if gaps else "")
    black = _detect(video, "blackdetect=d=0.4:pix_th=0.08", r"black_duration:([\d.]+)")
    add("no_black_frames", not black, f"{len(black)} trecho(s) preto(s)" if black else "")
    stray = sorted((set(info["tags"]) | set(info["stream_tags"])) - CLEAN_TAGS - {"language", "handler_name", "vendor_id"})
    add("metadata_clean", not stray, "Metadados restantes: " + ", ".join(stray) if stray else "")

    sheet, stamps = contact_sheet(video, sheet_dir or video.parent, info["duration"])
    failed = [c for c in checks if not c["ok"]]
    if failed:
        status = "FLAGGED"
    elif not visual:
        status = "PENDING_VISUAL"
    else:
        status = "APPROVED" if visual.get("verdict") == "pass" else "FLAGGED"
    return {"stage": "post", "status": status, "platform": platform, "video": str(video),
            "duration": round(info["duration"], 2), "checks": checks, "failed": [c["rule"] for c in failed],
            "visual": visual or {"verdict": "pending", "sheet": str(sheet), "frames_at": stamps,
                                 "look_for": ["logo TikTok/CapCut/Reels ou marca d'água de terceiros",
                                              "legendas antigas queimadas na origem",
                                              "cena gráfica nos 3 primeiros quadros (0,5-6,5 s)",
                                              "nossas legendas legíveis dentro da safe zone"]},
            "sheet": str(sheet)}


def vision_verdict(sheet, game):
    """Removido em 2026-11-21 (pedido do usuário): sem veredito visual por Gemini."""
    return None


def certificate(path, pre, post, extra=None):
    cert = {"status": "APPROVED" if pre.get("status") == "APPROVED" and post.get("status") == "APPROVED"
            else ("PENDING_VISUAL" if pre.get("status") == "APPROVED" and post.get("status") == "PENDING_VISUAL" else "FLAGGED"),
            "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"), "pre_flight": pre, "post_render": post, **(extra or {})}
    write_json(path, cert)
    return cert


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stage", required=True, choices=["pre", "post"])
    ap.add_argument("--game", default="gta6")
    ap.add_argument("--platform", default="shorts", choices=list(PLATFORMS))
    ap.add_argument("--plan")
    ap.add_argument("--video")
    ap.add_argument("--visual", choices=["pass", "fail"])
    ap.add_argument("--visual-notes", default="")
    ap.add_argument("--cert")
    a = ap.parse_args()
    game = load_game(a.game)
    if a.stage == "pre":
        report, plan = preflight(read_json(a.plan), game, a.platform)
        write_json(a.plan, plan)
        write_json(Path(a.plan).with_name("preflight.json"), report)
    else:
        video = Path(a.video)
        visual = {"verdict": a.visual, "notes": a.visual_notes, "by": "claude-code"} if a.visual else None
        report = postrender(video, game, a.platform, visual=visual)
        cert_path = Path(a.cert) if a.cert else video.with_name(video.stem + "_compliance.json")
        old = read_json(cert_path, {}) or {}
        pre = old.get("pre_flight") or {"stage": "pre", "status": "APPROVED", "note": "auditoria avulsa: sem plano de pré-flight"}
        cert = certificate(cert_path, pre, report, {k: old[k] for k in ("title", "flow", "game") if k in old})
        say(f"certificate: {cert_path} -> {cert['status']}")
    say(f"{a.stage.upper()}: {report['status']}" + (f" | failed: {', '.join(report['failed'])}" if report["failed"] else ""))
    for c in report["checks"]:
        if not c["ok"]:
            say(f"  x {c['rule']}: {c['detail']}")
    for f in report.get("auto_fixes", []):
        say(f"  fix: {f}")
    if report.get("sheet") and report["status"] == "PENDING_VISUAL":
        say(f"  inspect: {report['sheet']} then rerun with --visual pass|fail")
    sys.exit(0 if report["status"] in ("APPROVED", "PENDING_VISUAL") else 2)


if __name__ == "__main__":
    main()
