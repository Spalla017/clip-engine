"""Mine silent B-roll for a game from an official trailer or from your own gameplay recording.

  python -m src.asset_miner --game gta6 --source "URL or file" --kind official_trailer --tag cidade
  python -m src.asset_miner --game gta6 --list
  python -m src.asset_miner --game gta6 --find-cc "GTA 6 gameplay"     # CC BY gameplay, licence checked per video

Every clip is written without audio (-an) next to a .json with its origin. The compliance gate refuses
footage without that file, so leaked or unknown-origin video cannot enter a render.
"""
import argparse
import re
import sys
from pathlib import Path

from .common import FOOTAGE, WORK, cut, load_game, probe, read_json, run, say, slugify, write_json

KINDS = ["official_trailer", "official_screenshot", "own_gameplay", "licensed"]
CC_FILTER = "EgIwAQ%253D%253D"   # YouTube search filter: Creative Commons
# A CC BY upload only licenses what the uploader owns: reaction videos, compilations and streamer clips carry other
# people's footage and voices, so they are never a licensed source (2026-11-23).
NOT_THEIRS = re.compile(r"react|reaction|compilation|backlash|\bvs\b|asmongold|xqc|ishowspeed|kai ?cenat|moistcr1tikal|"
                        r"penguinz0|podcast|clips?\b|trailer|official", re.I)


def find_cc(query, limit=25, top=12):
    """CC BY videos for a query, each licence read from the video page (the search filter alone is not proof).
    Returns [{id, views, height, channel, title, license, date, no_commentary}] best first; drops NOT_THEIRS."""
    import json
    import urllib.parse
    url = f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(query)}&sp={CC_FILTER}"
    out = run(["yt-dlp", url, "--flat-playlist", "--playlist-end", str(limit), "--no-warnings", "--print", "%(id)s"],
              check=False, timeout=120).stdout.split()
    rows = []
    for vid in out[:limit]:
        d = run(["yt-dlp", "-J", "--skip-download", "--no-warnings", f"https://www.youtube.com/watch?v={vid}"],
                check=False, timeout=90).stdout
        try:
            d = json.loads(d)
        except ValueError:
            continue
        lic, title = d.get("license") or "", d.get("title") or ""
        if "creative commons" not in lic.lower() or NOT_THEIRS.search(title):
            continue
        rows.append({"id": vid, "views": d.get("view_count") or 0, "height": d.get("height") or 0,
                     "channel": d.get("channel") or "", "title": title, "license": lic, "date": d.get("upload_date"),
                     "no_commentary": bool(re.search(r"no commentary|sem coment|\bnc\b|4k gameplay|free roam", title, re.I))})
    rows.sort(key=lambda r: (-r["no_commentary"], -r["views"]))
    return rows[:top]


def download(url, out_dir, max_height=2160, video_only=False):
    """yt-dlp download. Returns (path, info dict).

    video_only=True is for B-roll: best picture up to max_height (4K), no audio track at all.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if video_only:
        fmt = f"bestvideo[height<={max_height}][ext=mp4]/bestvideo[height<={max_height}]"
        extra = []
    else:
        fmt = f"bv*[height<={max_height}]+ba/b[height<={max_height}]/bv*+ba/b"
        extra = ["--merge-output-format", "mp4"]
    run(["yt-dlp", "-f", fmt, *extra, "--no-playlist", "--no-warnings", "--write-info-json",
         "-o", str(out_dir / "source.%(ext)s"), url], "ytdlp", timeout=3600)
    info = read_json(out_dir / "source.info.json", {}) or {}
    files = [p for p in out_dir.glob("source.*") if p.suffix.lower() in (".mp4", ".webm", ".mkv", ".mov")]
    if not files:
        raise RuntimeError(f"yt-dlp não deixou vídeo em {out_dir}")
    return max(files, key=lambda p: p.stat().st_size), {
        "title": info.get("title", ""), "uploader": info.get("uploader") or info.get("channel", ""),
        "channel_id": info.get("channel_id", ""), "url": info.get("webpage_url", url),
        "duration": info.get("duration", 0), "language": info.get("language", ""),
        "license": info.get("license") or ""}


def scenes(video, min_len=2.0, max_len=5.0):
    """Shot list [(start, dur, shot_index)]. Long shots are split; the pieces keep the same shot_index."""
    cuts = []
    try:
        from scenedetect import ContentDetector, detect
        cuts = [(s.seconds, e.seconds) for s, e in detect(str(video), ContentDetector(threshold=27.0))]
    except Exception:
        out = run(["ffmpeg", "-hide_banner", "-nostats", "-i", video, "-vf", "select='gt(scene,0.35)',showinfo",
                   "-an", "-f", "null", "-"], check=False).stderr
        times = [0.0] + [float(t) for t in re.findall(r"pts_time:([\d.]+)", out)] + [probe(video)["duration"]]
        cuts = list(zip(times, times[1:]))
    if not cuts:
        cuts = [(0.0, probe(video)["duration"])]
    out = []
    for shot, (start, end) in enumerate(cuts):
        start, end = start + 0.08, end - 0.08  # stay clear of the transition frames
        while end - start >= min_len:
            dur = min(max_len, end - start)
            out.append((round(start, 3), round(dur, 3), shot))
            start += dur
    return out


def mine(game_id, source, kind, tag, limit=60, skip_start=0.0, skip_end=0.0, min_len=1.2, max_len=5.0,
         max_height=2160):
    """Cut every shot of an official video into silent clips (native resolution up to 4K)."""
    game = load_game(game_id)
    if kind not in game.get("allowed_footage", KINDS):
        sys.exit(f"'{kind}' is not allowed footage for {game['name']}: {game.get('allowed_footage')}")
    info = {"url": source, "title": Path(source).stem, "uploader": ""}
    video = Path(source)
    if re.match(r"https?://", source):
        video, info = download(source, WORK / "_miner" / slugify(source[-40:]), max_height, video_only=True)
        # "licensed" from a URL must say so on the video page (CC BY); anything else needs written permission and is
        # mined from a local file with the permission stored next to it (references/live-streamer.md).
        if kind == "licensed" and "creative commons" not in (info.get("license") or "").lower():
            sys.exit(f"Recusado: {source} não é Creative Commons (licença: '{info.get('license') or 'padrão do YouTube'}'). "
                     "Sem CC BY só com permissão escrita do criador.")
    vinfo = probe(video)
    total = vinfo["duration"]
    out_dir = FOOTAGE / game_id / tag
    out_dir.mkdir(parents=True, exist_ok=True)
    base = slugify(info.get("title") or video.stem, 44)
    made = 0
    for start, dur, shot in scenes(video, min_len, max_len):
        if start < skip_start or start + dur > total - skip_end:
            continue
        dst = out_dir / f"{base}_{int(start * 10):06d}.mp4"
        if not dst.exists():
            cut(video, dst, start, dur, audio=False, height=min(vinfo["height"], max_height, 1440))  # 1440p: sharp magnifier, 3x faster render than 4K
            write_json(dst.with_suffix(".json"), {
                "kind": kind, "tag": tag, "game": game_id, "source_url": info["url"],
                "source_title": info.get("title", ""), "uploader": info.get("uploader", ""),
                "start": start, "dur": dur, "shot": f"{base}#{shot}", "height": min(vinfo["height"], max_height),
                "license": info.get("license", ""),
                "credit": (f'Gameplay: "{info.get("title", "")}" by {info.get("uploader", "")} ({info["url"]}), CC BY'
                           if "creative commons" in (info.get("license") or "").lower() else "")})
        made += 1
        if made >= limit:
            break
    say(f"{made} clips ({kind}, sem áudio, {min(vinfo['height'], max_height)}p) -> {out_dir}")
    return made


def sheet(game_id, tag, out=None, cols=2):
    """One labelled sheet of a tag: 3 frames per clip (start, middle, end) so end-of-clip logos are visible."""
    from PIL import Image, ImageDraw
    from .common import ffmpeg
    clips = [c for c in library(game_id) if c["meta"].get("tag") == tag]
    tw, th = 160, 90
    rows = -(-len(clips) // cols)
    img = Image.new("RGB", (cols * tw * 3, max(1, rows) * th))
    draw = ImageDraw.Draw(img)
    tmp = WORK / "_sheet.jpg"
    for i, c in enumerate(clips):
        x0, y0 = (i % cols) * tw * 3, (i // cols) * th
        for k, t in enumerate((0.1, c["dur"] / 2, max(0.1, c["dur"] - 0.15))):
            ffmpeg(["-ss", f"{t:.2f}", "-i", c["path"], "-frames:v", "1", "-vf", f"scale={tw}:{th}", tmp], "sheet")
            img.paste(Image.open(tmp), (x0 + k * tw, y0))
        draw.rectangle([x0, y0, x0 + 46, y0 + 13], fill="black")
        draw.text((x0 + 2, y0 + 1), f"{i} {c['dur']:.1f}s", fill="yellow")
    tmp.unlink(missing_ok=True)
    out = Path(out or WORK / f"sheet_{game_id}_{tag}.jpg")
    img.save(out, quality=88)
    write_json(out.with_suffix(".json"), [c["path"] for c in clips])
    say(f"{len(clips)} clipes -> {out} (índices em {out.with_suffix('.json')})")
    return out


def apply_tags(game_id, sheet_json, mapping):
    """Sort clips after looking at a sheet. mapping: {"index": "tag" | "drop" | {"tag": t, "hide_bottom": 0.12}}."""
    import shutil
    paths = read_json(sheet_json)
    moved = dropped = 0
    for idx, value in mapping.items():
        src = Path(paths[int(idx)])
        side = src.with_suffix(".json")
        if value == "drop":
            src.unlink(missing_ok=True)
            side.unlink(missing_ok=True)
            dropped += 1
            continue
        value = {"tag": value} if isinstance(value, str) else value
        meta = read_json(side)
        meta.update(value)
        write_json(side, meta)
        dst_dir = FOOTAGE / game_id / meta["tag"]
        dst_dir.mkdir(parents=True, exist_ok=True)
        if dst_dir != src.parent:
            shutil.move(str(src), str(dst_dir / src.name))
            shutil.move(str(side), str(dst_dir / side.name))
        moved += 1
    say(f"{moved} clipes etiquetados, {dropped} descartados")


def library(game_id):
    """All usable clips for a game: [{path, dur, meta}]. Clips without a sidecar get kind 'unknown'."""
    out = []
    for path in sorted((FOOTAGE / game_id).rglob("*.mp4")):
        meta = read_json(path.with_suffix(".json"), None) or {"kind": "unknown", "tag": path.parent.name}
        if meta.get("blocked"):  # kept on disk, never picked (native rating card, suggestive moment...): see "blocked_reason"
            continue
        out.append({"path": str(path), "dur": meta.get("dur") or probe(path)["duration"], "meta": meta})
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game", default="gta6")
    ap.add_argument("--source", help="URL (official channel) or local file (your recording)")
    ap.add_argument("--kind", choices=KINDS)
    ap.add_argument("--tag", help="scene tag, see footage_tags in games/<game>.json")
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--skip-start", type=float, default=0.0, help="seconds to ignore at the start (logos, ratings)")
    ap.add_argument("--skip-end", type=float, default=0.0, help="seconds to ignore at the end (end cards)")
    ap.add_argument("--min-len", type=float, default=1.2, help="shortest clip kept, seconds")
    ap.add_argument("--max-height", type=int, default=2160, help="download and keep up to this height (4K)")
    ap.add_argument("--sheet", metavar="TAG", help="build a 3-frames-per-clip sheet of a tag")
    ap.add_argument("--apply", nargs=2, metavar=("SHEET_JSON", "MAPPING_JSON"), help="tag/drop clips by sheet index")
    ap.add_argument("--max-len", type=float, default=5.0)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--find-cc", metavar="QUERY", help="list CC BY videos for a query (licence checked per video)")
    a = ap.parse_args()
    if a.find_cc:
        for r in find_cc(a.find_cc):
            say(f"{r['id']} | {r['views']:>8} views | {r['height']}p | {r['date']} | {'sem voz' if r['no_commentary'] else 'com voz'} | "
                f"{r['channel'][:22]} | {r['title'][:70]}")
        say("Minerar: --source https://www.youtube.com/watch?v=<id> --kind licensed --tag <tag>; crédito vai no _POST.md (nunca na tela).")
        return
    if a.list:
        tags = {}
        for c in library(a.game):
            key = (c["meta"].get("tag", "?"), c["meta"].get("kind", "unknown"))
            tags[key] = tags.get(key, 0) + 1
        for (tag, kind), n in sorted(tags.items()):
            say(f"{tag:16} {kind:18} {n}")
        if not tags:
            say(f"Sem footage para {a.game}. Rode: python -m src.asset_miner --game {a.game} --source URL --kind official_trailer --tag cidade")
        return
    if a.sheet:
        sheet(a.game, a.sheet)
        return
    if a.apply:
        apply_tags(a.game, a.apply[0], read_json(a.apply[1]))
        return
    if not (a.source and a.kind and a.tag):
        ap.error("--source, --kind and --tag are required")
    mine(a.game, a.source, a.kind, a.tag, a.limit, a.skip_start, a.skip_end, a.min_len, a.max_len, a.max_height)


if __name__ == "__main__":
    main()
