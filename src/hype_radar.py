"""Trend radar for one game: Reddit (top of the day + hot), Google News RSS, YouTube search via yt-dlp.

Drops topics already published (data/history.json) and anything about leaks or stolen builds.

  python -m src.hype_radar --game gta6 --top 10
"""
import argparse
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from .common import DATA, WORK, history_load, load_game, read_json, run, say, slugify, write_json

CACHE = DATA / "reddit_cache.json"

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) clip-radar/1.0"}


API_UA = {"User-Agent": "windows:cortes-games-cli:v1.0", "Accept": "application/json"}
_LAST = {}


def _get(url, timeout=20, headers=None, gap=6.0):
    """GET with a per-host pause and one retry on 429 (Reddit limits bursts)."""
    host = urllib.parse.urlparse(url).netloc
    for attempt in (1, 2):
        wait = _LAST.get(host, 0) + gap - time.time()
        if wait > 0:
            time.sleep(wait)
        _LAST[host] = time.time()
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers or UA), timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 2:
                raise
            time.sleep(25)


def _reddit_atom(sub, kind):
    path = "top/.rss?t=day" if kind == "top" else "hot/.rss"
    root = ET.fromstring(_get(f"https://www.reddit.com/r/{sub}/{path}"))
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out = []
    for n, e in enumerate(root.findall("a:entry", ns)):
        link = e.find("a:link", ns)
        text = re.sub(r"<[^>]+>|&[#\w]+;", " ", e.findtext("a:content", "", ns))
        text = re.sub(r"submitted by\s+/u/\S+|\[link\]|\[comments\]", " ", text)
        out.append({"title": e.findtext("a:title", "", ns), "url": link.get("href") if link is not None else "",
                    "source": f"r/{sub}", "heat": (3000 if kind == "top" else 2800) - 110 * n,
                    "text": re.sub(r"\s+", " ", text).strip()[:500]})
    return out


def _reddit_json(sub, kind):
    q = "top.json?t=day&limit=15" if kind == "top" else "hot.json?limit=15"
    data = json.loads(_get(f"https://old.reddit.com/r/{sub}/{q}", headers=API_UA))
    out = []
    for c in data.get("data", {}).get("children", []):
        d = c["data"]
        if d.get("stickied") or d.get("over_18"):
            continue
        out.append({"title": d["title"], "url": "https://www.reddit.com" + d["permalink"], "source": f"r/{sub}",
                    "heat": 1500 + d.get("score", 0) + 2 * d.get("num_comments", 0), "text": (d.get("selftext") or "")[:500]})
    return out


def _reddit_pullpush(sub, kind):
    data = json.loads(_get(f"https://api.pullpush.io/reddit/search/submission/?subreddit={sub}&sort=desc&size=15", headers=API_UA))
    return [{"title": d.get("title", ""), "url": "https://www.reddit.com" + d.get("permalink", ""), "source": f"r/{sub}",
             "heat": 2600 - 110 * n, "text": (d.get("selftext") or "")[:500]}
            for n, d in enumerate(data.get("data", [])) if not d.get("over_18")]


def reddit(sub, kind="top"):
    """Posts of a subreddit. Tries, in order: public Atom feed, old.reddit JSON, PullPush archive.

    Reddit allows very few anonymous requests per minute, so results are cached for 30 minutes.
    """
    cache = read_json(CACHE, {}) or {}
    hit = cache.get(f"{sub}/{kind}")
    if hit and time.time() - hit["at"] < 1800:
        REDDIT_LOG.append(f"{sub}/{kind}/cache: {len(hit['items'])}")
        return hit["items"]
    for name, fn in (("atom", _reddit_atom), ("json", _reddit_json), ("pullpush", _reddit_pullpush)):
        try:
            items = [i for i in fn(sub, kind) if i["title"]]
        except Exception as e:
            REDDIT_LOG.append(f"{sub}/{kind}/{name}: {type(e).__name__} {getattr(e, 'code', '')}".strip())
            continue
        if items:
            REDDIT_LOG.append(f"{sub}/{kind}/{name}: {len(items)}")
            cache[f"{sub}/{kind}"] = {"at": time.time(), "items": items}
            write_json(CACHE, cache)
            return items
    return []


REDDIT_LOG = []


def news(query, locale="hl=en-US&gl=US&ceid=US:en"):
    url = "https://news.google.com/rss/search?q=" + urllib.parse.quote(query + " when:2d") + "&" + locale
    try:
        root = ET.fromstring(_get(url))
    except Exception:
        return []
    return [{"title": i.findtext("title", ""), "url": i.findtext("link", ""), "source": "google-news",
             "heat": 2500 - 110 * n, "text": re.sub(r"<[^>]+>", " ", i.findtext("description", ""))[:400]}
            for n, i in enumerate(root.iter("item")) if n < 20]


def youtube(query, n=15):
    proc = run(["yt-dlp", f"ytsearch{n}:{query}", "--dump-json", "--flat-playlist", "--no-warnings"],
               "radar", check=False, timeout=120)
    out = []
    for line in proc.stdout.splitlines():
        try:
            v = json.loads(line)
        except ValueError:
            continue
        out.append({"title": v.get("title", ""), "url": v.get("url") or v.get("webpage_url", ""), "source": "youtube",
                    "heat": int((v.get("view_count") or 0) ** 0.5), "text": (v.get("channel") or "")})
    return out


def scan(game, top=10):
    items = []
    for sub in game.get("subreddits", []):
        items += reddit(sub, "top") or reddit(sub, "hot")
    items += news(game.get("news_query", f'"{game["name"]}"'), game.get("news_lang") or "hl=en-US&gl=US&ceid=US:en")
    items += youtube(game.get("youtube_query", game["name"] + " shorts"))
    done = {h.get("topic_slug") for h in history_load()}
    leak = game.get("leak_terms", [])
    seen, keep, dropped = set(), [], {"leak": 0, "published": 0}
    for it in sorted(items, key=lambda x: -x["heat"]):
        slug = slugify(it["title"])
        if not it["title"] or slug in seen:
            continue
        seen.add(slug)
        blob = (it["title"] + " " + it["text"]).lower()
        if any(t in blob for t in leak):
            dropped["leak"] += 1
            continue
        # Not the game itself: mods of older games, fan concepts, memes (radar_exclude in the profile).
        if any(re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", blob) for t in game.get("radar_exclude", [])):
            dropped["off_topic"] = dropped.get("off_topic", 0) + 1
            continue
        if slug in done:
            dropped["published"] += 1
            continue
        keep.append({**it, "topic_slug": slug})
    return keep[:top], dropped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default="gta6")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--lang", default="en", choices=["en", "pt"])
    a = ap.parse_args()
    from .common import localize
    game = localize(load_game(a.game), a.lang)
    topics, dropped = scan(game, a.top)
    out = write_json(WORK / f"hype_{a.game}.json", {"game": game["name"], "topics": topics, "dropped": dropped})
    for i, t in enumerate(topics, 1):
        say(f"{i:2}. [{t['source']} {t['heat']}] {t['title'][:110]}")
    say(f"reddit: {'; '.join(REDDIT_LOG) or 'sem subreddits'}")
    say(f"dropped: {dropped} | saved: {out}")


if __name__ == "__main__":
    main()
