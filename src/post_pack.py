"""Delivery package for an approved clip: thumbnails (9:16 + 16:9) and a posting block for YouTube and TikTok.

The director (modo 1) writes work/<slug>_post.json after the video is APPROVED:
  {"titles": [["<title>", "<lever>"], ... 3 to 8],          # the 3 best (title.py score) are delivered
   "thumb_text": "PAID TO *FAIL*",                           # <= 3 words, *word* = accent colour, never the title
   "description": "<line 1 hook <= 100 chars>\\n<line 2>\\n<question>",
   "hashtags_youtube": ["#shorts", ...],                      # 3-5, optional (radar/profile otherwise)
   "tags": ["main search", ...],                              # 12-20, <= 450 chars, optional (radar otherwise)
   "pinned": ["<recommended pinned comment>", "<alternative>"],
   "tiktok_caption": "<= 150 chars", "hashtags_tiktok": ["#..."],
   "thumb_mode": "frame0" | "compose",                         # frame0 (default since 2026-11-18) = frame of the video
   "thumb_at": 0.5,                                           # frame0: second of the cover frame
   "frame": "<clip path>@<seconds>",                          # compose: force the thumbnail frame
   "text_side_16x9": "left" | "right"}                        # optional: where the words go on the 16:9 cover

  python -m src.post_pack output/<name>.mp4 --pack work/<slug>_post.json [--pick N]

Writes output/<name>_thumb_9x16.jpg, _thumb_16x9.jpg, _thumb_check.jpg (both at 160 px: still readable?),
_thumb_candidates.jpg and output/<name>_POST.md, and prints the copyable block.
The thumbnail is a new composition from the official footage the video used, never a frame of our render
(no captions, no headline, no third-party logo).
"""
import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageStat

from .common import ROOT, read_json, say

FONT = ROOT / "remotion-engine" / "public" / "fonts" / "Bangers-Regular.ttf"
SCHEDULE = ("YouTube (BRT): qui 18h30 · sex 17h00 · sáb/dom 18h30 · qua 19h00\n"
            "TikTok (BRT): sáb/dom 10h30 ou 14h30 · seg/ter/qui/sex 14h30 ou 20h00")


def _frame(clip, t, width=None):
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "f.png"
        vf = ["-vf", f"scale={width}:-2"] if width else []
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(clip), "-frames:v", "1", *vf, str(out)],
                       check=True)
        return Image.open(out).convert("RGB").copy()


def _trim_bars(img, thresh=14):
    """Drop the letterbox bars of cinemascope trailers (rows almost black from edge to edge)."""
    g = img.convert("L")
    w, h = g.size
    rows = [ImageStat.Stat(g.crop((0, y, w, y + 1))).mean[0] for y in range(h)]
    top = next((y for y in range(h // 3) if rows[y] > thresh), 0)
    bot = next((y for y in range(h - 1, h * 2 // 3, -1) if rows[y] > thresh), h - 1)
    return img.crop((0, top, w, bot + 1)) if (top or bot < h - 1) else img


def _score(img):
    """Thumbnail-worthiness of a small frame: bright enough, contrasty, sharp, colourful."""
    g = img.convert("L")
    mean = ImageStat.Stat(g).mean[0] / 255
    contrast = ImageStat.Stat(g).stddev[0] / 128
    edges = ImageStat.Stat(g.filter(ImageFilter.FIND_EDGES)).mean[0] / 40
    r, gg, b = [ImageStat.Stat(c).stddev[0] for c in img.split()]
    colour = (abs(r - gg) + abs(gg - b)) / 60 + ImageStat.Stat(img.convert("HSV").split()[1]).mean[0] / 255
    light = 1 - min(abs(mean - 0.5) / 0.35, 1)
    return round(light * 2 + contrast + min(edges, 1.5) + colour, 3)


def candidates(job, game, n=6):
    plan = read_json(job / "plan_v1.json", {}) or {}
    skip = set(game.get("fallback_exclude_tags", []))
    out = []
    for b in plan.get("broll", []):
        meta = b.get("meta", {})
        if meta.get("tag") in skip or not Path(b["path"]).exists():
            continue
        t = max(0.1, b.get("dur", 1) * 0.5)
        try:
            small = _frame(b["path"], t, 480)
        except subprocess.CalledProcessError:
            continue
        out.append({"path": b["path"], "t": t, "score": _score(small), "small": small, "hide_bottom": meta.get("hide_bottom", 0)})
    out.sort(key=lambda c: -c["score"])
    return out[:n]


def _sheet(imgs, path, width=160):
    tiles = [i.resize((width, round(i.height * width / i.width))) for i in imgs]
    h = max(t.height for t in tiles)
    sheet = Image.new("RGB", (sum(t.width for t in tiles) + 8 * (len(tiles) - 1), h), "white")
    x = 0
    for i, t in enumerate(tiles):
        sheet.paste(t, (x, 0))
        ImageDraw.Draw(sheet).text((x + 4, 2), str(i), fill="yellow")
        x += t.width + 8
    sheet.save(path, quality=90)


def _crop(img, ratio, hide_bottom=0.0):
    w, h = img.size
    usable = int(h * (1 - hide_bottom))
    if w / usable > ratio:
        cw = int(usable * ratio)
        return img.crop(((w - cw) // 2, 0, (w - cw) // 2 + cw, usable))
    ch = int(w / ratio)
    return img.crop((0, max(0, (usable - ch) // 2), w, max(0, (usable - ch) // 2) + ch))


def _grade(img):
    img = ImageEnhance.Contrast(img).enhance(1.15)
    img = ImageEnhance.Color(img).enhance(1.25)
    return ImageEnhance.Sharpness(img).enhance(1.3)


def _shade(img, top=0.0, bottom=0.0, left=0.0):
    """Dark gradients where the text sits, so white words stay readable on any frame."""
    w, h = img.size
    mask = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(mask)
    for y in range(h):
        a = 0
        if top and y < h * top:
            a = max(a, int(200 * (1 - y / (h * top))))
        if bottom and y > h * (1 - bottom):
            a = max(a, int(170 * (y - h * (1 - bottom)) / (h * bottom)))
        if a:
            d.line([(0, y), (w, y)], fill=a)
    if left:
        for x in range(int(w * left)):
            a = int(190 * (1 - x / (w * left)))
            d.line([(x, 0), (x, h)], fill=max(a, 0))
    return Image.composite(Image.new("RGB", (w, h), "black"), img, mask)


def _words(text):
    """'PAID TO *FAIL*' -> [('PAID', False), ('TO', False), ('FAIL', True)]"""
    return [(w.strip("*").upper(), w.startswith("*")) for w in text.split()]


def _fit(d, lines, bw, bh):
    size = 520
    while size > 40:
        font = ImageFont.truetype(str(FONT), size)
        sp = d.textlength(" ", font=font)
        widths = [sum(d.textlength(w, font=font) for w, _ in ln) + sp * (len(ln) - 1) for ln in lines]
        if max(widths) <= bw and size * 0.98 * len(lines) <= bh:
            return size, font, widths
        size -= 8
    return size, font, widths


def _splits(words, max_lines=3):
    """Every way to break the words into 1..max_lines consecutive lines."""
    n = len(words)
    out = [[words]]
    for k in range(1, min(max_lines, n)):
        from itertools import combinations
        for cuts in combinations(range(1, n), k):
            idx = (0, *cuts, n)
            out.append([words[idx[i]:idx[i + 1]] for i in range(len(idx) - 1)])
    return out


def _draw_text(img, text, accent, box, max_lines=3):
    """Pick the line breaks that give the biggest font inside box (x, y, w, h); white, *accent*, thick stroke."""
    x0, y0, bw, bh = box
    d = ImageDraw.Draw(img)
    size, font, widths, lines = max((_fit(d, ln, bw, bh) + (ln,) for ln in _splits(_words(text), max_lines)),
                                    key=lambda r: r[0])
    lh = size * 0.98
    stroke = max(6, size // 11)
    y = y0 + (bh - lh * len(lines)) / 2
    sp = d.textlength(" ", font=font)
    for ln, lw in zip(lines, widths):
        x = x0 + (bw - lw) / 2
        for w, hot in ln:
            d.text((x + stroke * 0.6, y + stroke * 0.8), w, font=font, fill="#000000")
            d.text((x, y), w, font=font, fill=accent if hot else "#FFFFFF", stroke_width=stroke, stroke_fill="#000000")
            x += d.textlength(w, font=font) + sp
        y += lh
    return size


def _calm_side(img):
    """'left' or 'right': the half with less detail, where the words go on the 16:9 cover."""
    g = img.convert("L").resize((320, 180)).filter(ImageFilter.FIND_EDGES)
    l = ImageStat.Stat(g.crop((0, 0, 160, 180))).mean[0]
    r = ImageStat.Stat(g.crop((160, 0, 320, 180))).mean[0]
    return "left" if l <= r else "right"


def thumbnails_frame0(video, pack, name):
    """Thumbnail = a frame of the video itself, overlays burned in (user's model Shorts, 2026-11-18).
    The hook frame is built to be the cover; "thumb_at" (s) picks it, default 0.5 s (stickers settled, first word
    on screen). Also writes the first 3 s as a 6-frame sheet to choose from, and a 16:9 cover (the 9:16 frame over a
    blurred copy of itself, no new text)."""
    times = [0.5, 0.9, 1.4, 1.9, 2.4, 2.9]
    t = float(pack.get("thumb_at", 0.5))
    frames = [_frame(video, x) for x in times]
    _sheet(frames, ROOT / "output" / f"{name}_thumb_candidates.jpg", 160)
    v = _frame(video, t).convert("RGB").resize((1080, 1920), Image.LANCZOS)
    bg = _crop(v, 16 / 9).resize((1280, 720), Image.LANCZOS).filter(ImageFilter.GaussianBlur(28))
    bg = ImageEnhance.Brightness(bg).enhance(0.55)
    fg = v.resize((405, 720), Image.LANCZOS)
    bg.paste(fg, ((1280 - 405) // 2, 0))
    out9, out16 = ROOT / "output" / f"{name}_thumb_9x16.jpg", ROOT / "output" / f"{name}_thumb_16x9.jpg"
    v.save(out9, quality=92)
    bg.save(out16, quality=92)
    _sheet([v, bg], ROOT / "output" / f"{name}_thumb_check.jpg", 160)
    say(f"  capa (quadro do vídeo @{t:.1f}s): {out9.name} + {out16.name}; outros quadros em {name}_thumb_candidates.jpg")
    return out9, out16


def thumbnails(job, game, pack, name, pick=0):
    accent = game.get("accent", "#FFD400")
    if pack.get("frame"):
        path, t = pack["frame"].rsplit("@", 1)
        chosen = {"path": path, "t": float(t), "hide_bottom": (read_json(Path(path).with_suffix(".json"), {}) or {}).get("hide_bottom", 0)}
    else:
        cands = candidates(job, game)
        if not cands:
            sys.exit("Sem quadro candidato: passe \"frame\": \"<clipe>@<s>\" no pack.")
        _sheet([c["small"] for c in cands], ROOT / "output" / f"{name}_thumb_candidates.jpg", 240)
        chosen = cands[min(pick, len(cands) - 1)]
    full = _grade(_trim_bars(_frame(chosen["path"], chosen["t"])))
    text = pack["thumb_text"]
    if len(_words(text)) > 3:
        say("  aviso: texto da capa com mais de 3 palavras")
    v = _shade(_crop(full, 9 / 16, chosen.get("hide_bottom", 0)).resize((1080, 1920), Image.LANCZOS), top=0.45, bottom=0.18)
    sv = _draw_text(v, text, accent, (40, 150, 1000, 700), max_lines=2)  # top third, clear of the Shorts UI
    h = _crop(full, 16 / 9, chosen.get("hide_bottom", 0)).resize((1280, 720), Image.LANCZOS)
    side = pack.get("text_side_16x9") or _calm_side(h)
    if side == "right":
        h = _shade(h.transpose(Image.FLIP_LEFT_RIGHT), left=0.6).transpose(Image.FLIP_LEFT_RIGHT)
    else:
        h = _shade(h, left=0.6)
    sh = _draw_text(h, text, accent, (30 if side == "left" else 610, 50, 640, 620))
    out9, out16 = ROOT / "output" / f"{name}_thumb_9x16.jpg", ROOT / "output" / f"{name}_thumb_16x9.jpg"
    v.save(out9, quality=92)
    h.save(out16, quality=92)
    _sheet([v, h], ROOT / "output" / f"{name}_thumb_check.jpg", 160)
    say(f"  capa: {out9.name} (fonte {sv}px) + {out16.name} (fonte {sh}px) de {Path(chosen['path']).name}@{chosen['t']:.1f}s")
    return out9, out16


def _lint(title):
    from .trend_panel import lint_title
    try:
        return lint_title(title)[0]
    except Exception:  # noqa: BLE001 - lint is advisory
        return 0


def _relevant(tags, pack, game):
    """Radar tags only when they share a topic word with the video (lesson 2026-11-18: the weekly radar put
    'strip club graphics' and streamer names on a GTA 5 vs GTA 6 comparison; an unrelated tag is misleading)."""
    stop = set(re.findall(r"[a-z0-9]+", (game.get("name", "") + " " + " ".join(game.get("aliases", []))).lower())) | {
        "gta", "6", "vi", "5", "v", "the", "and", "of", "in", "a", "to", "game", "games", "new", "grand", "theft", "auto"}
    text = " ".join([pack.get("description", "")] + [t if isinstance(t, str) else t[0] for t in pack.get("titles", [])]).lower()
    words = set(re.findall(r"[a-z0-9]+", text)) - stop
    keep = [t for t in tags if set(re.findall(r"[a-z0-9]+", t.lower())) & words or t.lower().strip() in ("gta 6", "gta vi")]
    if len(keep) < len(tags):
        say(f"  tags do radar sem relação com o vídeo descartadas: {len(tags) - len(keep)}")
    return keep


def package(meta_txt, game, pack, name):
    from .trend_panel import seo_pack
    seo = seo_pack(game.get("name", "")) or {}
    titles = []
    for t in pack.get("titles", []):
        title, lever = (t, "") if isinstance(t, str) else (t[0], t[1] if len(t) > 1 else "")
        titles.append((title, lever, _lint(title)))
    titles.sort(key=lambda x: -x[2])
    best = titles[:3]
    yt_tags = pack.get("hashtags_youtube") or (seo.get("hashtags_youtube") or game.get("hashtags", []))[:5]
    tt_tags = pack.get("hashtags_tiktok") or (seo.get("hashtags_tiktok") or [])[:5]
    tags = pack.get("tags") or _relevant(seo.get("hidden_tags") or [], pack, game)
    tag_line = ", ".join(tags)
    while len(tag_line) > 450 and tags:
        tags = tags[:-1]
        tag_line = ", ".join(tags)
    sources = re.search(r"^(Sources|Fontes): .*$", meta_txt, re.M)
    desc = pack.get("description", "").strip()
    lines = [f"# Pacote de postagem: {name}", "", "## YouTube", "", "**Títulos (3 melhores):**"]
    for i, (t, lever, score) in enumerate(best, 1):
        lines.append(f"{i}. {t}  ({len(t)} car. · nota {score}" + (f" · {lever}" if lever else "") + ")")
    lines += ["", "**Descrição:**", "```", desc, "", f"Not affiliated with {(game.get('publisher') or 'the publisher').split('/')[0].strip()}.",
              sources.group(0) if sources else "", "", " ".join(yt_tags), "```", "",
              f"**Tags ocultas ({len(tags)} · {len(tag_line)} caracteres):**", "```", tag_line, "```", ""]
    if pack.get("pinned"):
        lines += ["**Comentário fixado:**"] + [f"{'Recomendado' if i == 0 else 'Alternativa'}: {p}" for i, p in enumerate(pack["pinned"])] + [""]
    lines += ["## TikTok", "", "```", (pack.get("tiktok_caption", "") + " " + " ".join(tt_tags)).strip(), "```", "",
              "## Agenda", "", SCHEDULE, "", "## Capa", "",
              f"`{name}_thumb_9x16.jpg` (Short/TikTok: no TikTok, escolha a capa no segundo {pack.get('thumb_at', 0.5)}) · `{name}_thumb_16x9.jpg` (busca/feed)"]
    out = ROOT / "output" / f"{name}_POST.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video", help="output/<name>.mp4 (approved)")
    ap.add_argument("--pack", required=True)
    ap.add_argument("--pick", type=int, default=0, help="thumbnail candidate index from _thumb_candidates.jpg")
    a = ap.parse_args()
    video = Path(a.video)
    name = video.stem
    if not (video.with_name(f"{name}_compliance.json")).exists():
        sys.exit("Só vídeo aprovado em output/ (falta o _compliance.json).")
    cert = read_json(video.with_name(f"{name}_compliance.json"), {}) or {}
    gid = cert.get("game") or name.split("_")[0]
    game = read_json(ROOT / "games" / f"{gid}.json", {}) or {}
    game.setdefault("id", gid)
    job = ROOT / "work" / re.sub(r"_v\d+$", "", name)
    pack = read_json(Path(a.pack), {}) or {}
    if pack.get("thumb_mode", "frame0") == "frame0":   # law since 2026-11-18; "compose" = old new-artwork cover
        thumbnails_frame0(video, pack, name)
    else:
        thumbnails(job, game, pack, name, a.pick)
    txt = video.with_suffix(".txt")
    post = package(txt.read_text(encoding="utf-8") if txt.exists() else "", game, pack, name)
    say(f"  pacote: {post}")
    print(post.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
