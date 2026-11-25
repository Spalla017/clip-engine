"""Radar behind the cortes-games panel: which games are hot, which clip style is winning, what to tag.

  python -m src.trend_panel [--refresh] [--game "GTA 6"]

Sources (all free, no login):
  - YouTube search, videos under 4 min uploaded this week, per game (yt-dlp): views = this week's attention
  - Steam "most played" chart: which games people are actually in right now
  - YouTube autocomplete: what people type after the game's name (real searches)
  - tags + hashtags of the week's most viewed videos of the top game
  - most mentioned themes per game: phrases repeated across the week's Shorts titles (different channels),
    Google News headlines (2 days) and the subreddit's hot posts, boosted by autocomplete / Google Trends
TikTok has no open data source: its numbers are NOT measured here. TikTok hashtags are derived from the
YouTube Shorts data, which is the closest public signal.

Writes data/panel.json (cached 3 h) and data/panel_widget.html (the interactive panel shown in Claude).
"""
import argparse
import html
import json
import re
import statistics
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .common import DATA, GAMES, read_json, run, say, write_json

PANEL = DATA / "panel.json"
WIDGET = DATA / "panel_widget.html"
WATCHLIST = DATA / "watchlist.json"
DEFAULT_WATCH = ["GTA 6", "Fortnite", "Minecraft", "Roblox", "Call of Duty", "EA Sports FC", "Valorant"]
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
WEEK_SHORT = "EgYIAxABGAE%253D"   # this week + video + under 4 minutes
ANY_SHORT = "EgQQARgB"            # video + under 4 minutes
WEEK_TOP = "CAMSBggDEAEYAQ%253D%253D"   # sorted by view count + this week + video + under 4 minutes (2026-11-23)
LIVE = "EgJAAQ%253D%253D"              # live now

# Clip styles, matched on titles. Order matters: first hit wins.
STYLES = [
    ("streamer", "Reação de streamer", r"ishowspeed|\bspeed\b|xqc|kai ?cenat|vanoss|nogla|penguinz0|moistcr1tikal|dr ?disrespect|asmongold|caseoh|jynxzi|ludwig|summit1g|shroud|\bninja\b|pokimane|valkyrae|adin ross|\breacts?\b|reaction|first time playing"),
    ("comparacao", "Comparação lado a lado", r"\bvs\.?\b|versus|comparison|compared|then and now|vs real life"),
    ("lista", "Lista / top N", r"\btop \d+|\b\d+ (best|funniest|scariest|craziest|things|moments|details|secrets|clips)\b|\branks?\b|ranking|tier list"),
    ("hype", "Cena oficial \"looks INSANE\"", r"looks (incredible|insane|amazing|unreal|real)|(is|are) insane|first look|new clips|gorgeous|graphics|\bis here\b"),
    ("momento", "Momento cru de jogo", r"funny|fail|moment|npc|glitch|bug\b|wtf|rage|clutch|insane|crazy|ragdoll|unlucky|luck|epic|satisfying|stunt|shorts? clip"),
    ("detalhes", "Detalhes e segredos", r"detail|secret|easter egg|you missed|hidden|did you know|facts?\b|things you|nobody noticed"),
    ("meme", "Meme / POV", r"\bpov\b|when you|be like|meme|\bedit\b|nobody:|me when"),
    ("dica", "Dica / resposta (how to, onde fica)", r"how to|tips?\b|guide|fastest|best way|tutorial|unlock|money method|where (is|to)|location|how many|how long|explained"),
    ("noticia", "Notícia / trailer", r"trailer|release|news|update|confirmed|launch|review|reveal|pre-?load|leak"),
]
HOW = {
    "comparacao": ('--compare A.mp4@s-e B.mp4@s-e --labels "X|Y"', "Dois clipes empilhados com rótulo; narração curta."),
    "momento": ("--clip-url URL --highlights --no-captions --pov \"...\"", "Trecho de pico de som de um vídeo com licença CC ou autorizado."),
    "detalhes": ('--topic "..." --length quick', "Narrado, com cards de lista e lupa no detalhe."),
    "meme": ('--clip-url URL --highlights --pov "POV: ..."', "Texto POV grande sobre gameplay."),
    "dica": ('--topic "..." --length quick', "Narrado em passos, com cards numerados."),
    "noticia": ('--topic "..." --target-platform shorts', "Notícia relâmpago (modelo TmarTn2): fato na 1ª frase, B-roll oficial a cada 1,5-2,5 s, cards de horário/tamanho, 1 bônus, pergunta no fim."),
    "lista": ('--topic "Top N ..." --length quick', "Enumerado (\"Number one:\") para ganhar cards de lista; nº 1 por último."),
    "hype": ('--topic "..." --length quick', "1 cena oficial forte + comentário curto (voz ≤ 25 %), trilha nossa no clima, corte seco em loop."),
    "streamer": ("--clip-url URL --highlights (só com permissão: references/live-streamer.md)", "O momento do streamer + a reação dele, 1-3 tomadas; sem permissão registrada, vire notícia sobre o que ele fez, sem a imagem dele."),
}

# Clip playbook per style, from the 2026-11-18 study of the top 10 YouTube + top 10 TikTok gaming clip accounts
# (references/estudo-clipadores-2026-11-18.md). Shown in the panel next to the recommendation.
PLAYBOOK = {
    "noticia": {"shorts": "35-55 s", "tiktok": "61,5-65 s", "shot": "1,5-2,5 s", "voice": "70-90 % do tempo", "music": "baixa ou nenhuma (trap/lo-fi)",
                "hook": "fato + jogo na 1ª frase: \"Rockstar just updated the GTA 6 …\"", "end": "pergunta curta e engraçada, corte seco", "ref": "TmarTn2 997 mil · tridzoid 639 mil"},
    "hype": {"shorts": "15-30 s", "tiktok": "61,5 s+ (ou < 60 s se for só a cena)", "shot": "2-3 s", "voice": "≤ 25 %", "music": "drum and bass ~170 BPM ou trap",
             "hook": "a cena mais forte no quadro 0 + riser", "end": "loop: último quadro emenda no primeiro", "ref": "restartsquare 19,8 mi · GTASeriesVideos 887 mil"},
    "lista": {"shorts": "25-45 s", "tiktok": "61,5-65 s", "shot": "2-3 s", "voice": "50-80 %", "music": "trap/lo-fi por baixo",
              "hook": "promessa do nº 1 (\"the last one is insane\")", "end": "nº 1 por último, corte seco", "ref": "gamelancer 2,1 mi"},
    "momento": {"shorts": "8-25 s", "tiktok": "< 60 s", "shot": "1 a 3 tomadas", "voice": "0-25 %", "music": "som do jogo ou trilha nossa",
                "hook": "começa 1-2 s antes do pico", "end": "reação e corte seco", "ref": "Ludwig 40 mi · KaiCenatLive 15 mi (só com gravação própria ou permissão)"},
}
REACH = {"hype": 1.4, "noticia": 1.25, "lista": 1.15, "dica": 1.1, "momento": 1.0, "detalhes": 1.0, "meme": 1.0, "streamer": 1.0, "comparacao": 0.5}
# dica 0.9 -> 1.1 on 2026-11-23: answer Shorts live on search (a "how to find X" GTA 6 Short did 704k that week, and the
# studied creators' Shorts that escaped the 1k jail came from search). Streamer reactions were the week's top Shorts but
# are only usable with the streamer's permission (references/live-streamer.md).
PLAYBOOK["detalhes"] = PLAYBOOK["lista"]
PLAYBOOK["dica"] = PLAYBOOK["lista"]
PLAYBOOK["meme"] = PLAYBOOK["momento"]
PLAYBOOK["comparacao"] = PLAYBOOK["hype"]
PLAYBOOK["streamer"] = PLAYBOOK["momento"]
ALWAYS = ["Quadro 0 entendível sem som + SFX de impacto (pop/riser/boom)", "Tomada média ≤ 2,5 s, corte no ataque da fala ou da batida",
          "Título ≤ 50 caracteres, 1 emoji no fim, 1-2 palavras em CAIXA ALTA", "Postar em privado, revisar o pacote, depois público",
          "Meta: 2 vídeos/dia por plataforma (líderes de notícia: 3-6/dia)", "No Studio: viewed vs swiped > 70 %"]


def _get(url, timeout=20):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def steam_top(n=6):
    """Most played games on Steam right now: [(name, concurrent players)]."""
    try:
        ranks = json.loads(_get("https://api.steampowered.com/ISteamChartsService/GetMostPlayedGames/v1/"))["response"]["ranks"][:n]
    except Exception:
        return []

    def name(r):
        try:
            d = json.loads(_get(f"https://store.steampowered.com/api/appdetails?appids={r['appid']}&filters=basic"))
            return d[str(r["appid"])]["data"]["name"], r.get("peak_in_game", 0)
        except Exception:
            return None

    with ThreadPoolExecutor(6) as ex:
        return [x for x in ex.map(name, ranks) if x]


def suggest(q):
    """What people type on YouTube after this text (autocomplete = real searches)."""
    try:
        url = "https://suggestqueries.google.com/complete/search?client=firefox&ds=yt&hl=en&q=" + urllib.parse.quote(q)
        return [s for s in json.loads(_get(url, 10))[1] if s.lower() != q.lower()][:10]
    except Exception:
        return []


def _search(q, sp, limit):
    url = f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(q)}&sp={sp}"
    out = run(["yt-dlp", url, "--flat-playlist", "--playlist-end", str(limit), "--no-warnings", "--print",
               "%(view_count)s|%(duration)s|%(id)s|%(channel)s|%(title)s"], check=False, timeout=90).stdout
    rows = []
    for line in out.splitlines():
        p = line.split("|", 4)
        if len(p) == 5 and p[0].isdigit() and p[1].replace(".", "").isdigit() and float(p[1]) <= 90:
            rows.append({"views": int(p[0]), "dur": float(p[1]), "id": p[2], "channel": p[3], "title": p[4]})
    return rows


def youtube(game, limit=50):
    """This week's short videos about a game: [{views, dur, id, channel, title}].
    Most viewed of the week first (sorted search; 2026-11-23: the relevance search alone swung 10x between runs),
    plus the relevance search, merged by id. No Short this week -> all-time shorts, flagged this_week False."""
    seen, rows = set(), []
    for q, sp in ((game, WEEK_TOP), (game + " shorts", WEEK_SHORT)):
        for r in _search(q, sp, limit):
            if r["id"] not in seen:
                seen.add(r["id"])
                rows.append(r)
    if len(rows) >= 6:
        return rows, True
    return _search(game + " shorts", ANY_SHORT, limit), False


def live(game, limit=20):
    """Live streams of the game on YouTube right now: [{viewers, channel, title}] of the top `limit`.
    The closest open signal of what is being PLAYED on YouTube today (Steam covers PC only)."""
    url = f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(game)}&sp={LIVE}"
    out = run(["yt-dlp", url, "--flat-playlist", "--playlist-end", str(limit), "--no-warnings", "--print",
               "%(concurrent_view_count)s|%(live_status)s|%(channel)s|%(title)s"], check=False, timeout=90).stdout
    rows = []
    for line in out.splitlines():
        p = line.split("|", 3)
        if len(p) == 4 and p[0].isdigit() and p[1] == "is_live":
            rows.append({"viewers": int(p[0]), "channel": p[2], "title": p[3]})
    return rows


def style_of(title):
    for key, _, pattern in STYLES:
        if re.search(pattern, title, re.I):
            return key
    return "outro"


# Publisher and big-media channels: their views show hype, but their videos are not a style we can copy.
OFFICIAL = re.compile(r"rockstar|playstation|xbox|nintendo|\bign\b|gamespot|netflix|epic ?games|mojang|riot ?games|ubisoft|activision|blizzard|\bea\b|\bsteam\b", re.I)
OFFICIAL_TAG = re.compile(r"^(rockstar\w*|playstation\w*|ps[45]|xbox\w*|nintendo\w*|ign|gamespot|netflix|epicgames|mojang|riotgames|ubisoft|activision|blizzard|ea|steam)$", re.I)
LEAK = ["leak", "leaked", "vazad", "vazou", "vazamento", "dev build", "pre-release", "source code", "stolen", "datamine"]


def leak_terms(game):
    """Generic leak words + the game profile's own list. Panel never suggests a tag, search or title with them."""
    terms = list(LEAK)
    for p in GAMES.glob("*.json"):
        g = read_json(p)
        if re.sub(r"\W", "", g.get("name", "").lower()) == re.sub(r"\W", "", game.lower()):
            terms += [t.lower() for t in g.get("leak_terms", [])]
    return terms


def clean(items, game):
    bad = leak_terms(game)
    return [x for x in items if not any(b in x.lower() for b in bad)]


# Title + description recipes per clip style (en-US). {g} game, {k} hottest search tail, {n} a number.
# Molds from the most viewed titles of the top clip accounts (2026-11-18): <= 50 chars, 1 emoji at the end, 1-2 CAPS words.
TITLES = {
    "noticia": ["{g} {k} Just Got UPDATED 🚨", "{g}'s {k} Will Be BIGGER Than You Think", "{g} {k}: what changed this week 🚨"],
    "momento": ["This {g} moment is INSANE 💀", "bro this {g} NPC is lowkey smarter than me", "{g} {k}: the clip everyone is sharing 🤯"],
    "detalhes": ["{n} {g} details you MISSED 😳", "{g} {k}: the detail nobody noticed", "{g} hides THIS in {n} seconds 🤯"],
    "comparacao": ["{g} vs real life: {n} spots side by side", "{g} {k}: then and now 😳", "{g} vs its last game in {n} shots"],
    "meme": ["POV: your first hour in {g} 💀", "{g} players when {k} drops", "Every {g} player in {n} seconds 💀"],
    "dica": ["{g} {k}: the FASTEST way", "{n} {g} tips nobody told you", "{g}: do this in your first {n} minutes 🤯"],
    "lista": ["Top {n} {g} {k} (the last one is INSANE)", "{n} {g} moments that feel scripted 💀", "Ranking every {g} {k} 🤯"],
    "hype": ["{g} {k} Looks INSANE 🤯", "{g} New Clips Are UNREAL 😳", "{g} {k}: FIRST LOOK 🚨"],
}
QUESTION = {
    "noticia": "Which detail are you most hyped for?", "momento": "Has this ever happened to you?",
    "detalhes": "Did you catch it the first time?", "comparacao": "Which one looks better to you?",
    "meme": "Be honest, is this you?", "dica": "Got a faster way? Drop it below.",
    "lista": "What would be your number one?", "hype": "Does this look real to you?",
}


def lint_title(t):
    """Score with the yt-package linter when installed (0-100); plain length check otherwise."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("ytp_title", Path.home() / ".claude/skills/yt-package/title.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        r = mod.check(t)
        return r["score"], [k for k, _ in r["issues"]]
    except Exception:
        return (90 if len(t) <= 60 else 50), []


def package(game, style, searches, hashtags_yt, topic=None):
    """3 linted titles (best first) + a short description with the comment question and hashtags.
    {k} = the game's most mentioned theme this week, else the hottest search tail."""
    pre = game.lower()
    tails = [s[len(pre):].strip() for s in searches if s.lower().startswith(pre) and len(s) > len(pre) + 2]
    k = (topic or (tails[0] if tails else "update")).title()
    out = []
    for t in TITLES.get(style, TITLES["noticia"]):
        title = t.format(g=game, k=k, n=5)
        score, issues = lint_title(title)
        if len(title) > 50:   # top clip titles are 25-45 chars; past 50 they get cut on phones
            score, issues = min(score, 60), issues + ["over_50_chars"]
        out.append({"title": title, "score": score, "issues": issues})
    out.sort(key=lambda x: -x["score"])
    q = QUESTION.get(style, QUESTION["noticia"])
    desc = (f"{out[0]['title']}. {q}\n\nClip source credited below. "
            f"Not affiliated with the publisher.\n\n{' '.join(hashtags_yt)}")
    return {"titles": out, "description": desc, "question": q}


# Most mentioned themes per game (user request 2026-11-23): phrases of 1-3 words repeated across this week's
# short-video titles of DIFFERENT channels, weighted by (capped) views, boosted when people also search them
# (YouTube autocomplete) or they are rising on Google Trends. Generic hype words, the game's own name, channel
# names, publisher names and leak words never become a theme.
_STOP = set("""a an the and or but of to in on at for from with by as is are was were be been being it its it's
this that these those i i'm you you're he she we they my your our their me him her us them what which who whom how why
when where there here so if then than too very can could will would should just not no yes do does did done get got gets
getting go going goes make makes made new best top most more all any every some one first last only still even really
ever never now today day week about into out up down over after before again off like look looks looking has have had
let lets let's vs know need want see watch watching ago way back much many way miss missed end ending win won kill kills
killed goodbye live stream round says said why here's everything finally officially confirmed report reportedly""".split())
_HYPE = set("""shorts short video videos clip clips game games gaming gamer gamers gameplay play playing played player players
viral fyp foryou foryoupage trending insane crazy funny epic moment moments wtf omg lol bro guys part ep episode official
real reaction reacts react amazing unreal incredible scary craziest funniest literally actually lowkey pov edit edits
meme memes subscribe gg ggs fun good bad big huge hard easy most thing things stuff someone something guy people time
times complete completed full walkthrough playthrough stream streaming live ao vivo vivo hoje gameplay commentary no
""".split())   # live-title filler words added 2026-11-23
_FOREIGN = set("de la el que en do da no na com para por um uma o e y los las se del al es mi tu su".split())


def aliases(game):
    """Compact names of a game: its name + the profile's aliases ("gta6", "gtavi", ...)."""
    names = [game]
    for p in GAMES.glob("*.json"):
        g = read_json(p)
        if re.sub(r"\W", "", g.get("name", "").lower()) == re.sub(r"\W", "", game.lower()):
            names += g.get("aliases", [])
    return {re.sub(r"[^a-z0-9]", "", n.lower()) for n in names} - {""}


def _game_tokens(game):
    names = [game]
    for p in GAMES.glob("*.json"):
        g = read_json(p)
        if re.sub(r"\W", "", g.get("name", "").lower()) == re.sub(r"\W", "", game.lower()):
            names += g.get("aliases", [])
    toks = set()
    for n in names:
        w = re.findall(r"[a-z0-9]+", n.lower())
        toks |= set(w) | {"".join(w)}
    return toks


def _words(title):
    t = re.sub(r"#\w+|https?://\S+|'s\b", " ", title.lower())
    return re.findall(r"[a-z0-9]+(?:'[a-z]+)?", t)


def news_titles(game):
    """Google News headlines about the game, last 2 days, US English (outlet name cut off)."""
    try:
        import xml.etree.ElementTree as ET
        url = "https://news.google.com/rss/search?q=" + urllib.parse.quote(f'"{game}" when:2d') + "&hl=en-US&gl=US&ceid=US:en"
        root = ET.fromstring(_get(url, 15))
    except Exception:
        return []
    return [{"title": re.sub(r"\s+-\s+[^-]+$", "", i.findtext("title", "")), "channel": "news:" + (i.findtext("source") or str(n)),
             "weight": 0, "kind": "news"} for n, i in enumerate(root.iter("item")) if n < 25]


def reddit_titles(game):
    """Hot posts of the game's first subreddit (only games with a profile in games/)."""
    for p in GAMES.glob("*.json"):
        g = read_json(p)
        if re.sub(r"\W", "", g.get("name", "").lower()) == re.sub(r"\W", "", game.lower()) and g.get("subreddits"):
            try:
                from .hype_radar import reddit
                return [{"title": i["title"], "channel": f"reddit:{n}", "weight": 0, "kind": "reddit"}
                        for n, i in enumerate(reddit(g["subreddits"][0].replace("r/", ""), "hot")[:25])]
            except Exception:
                return []
    return []


SPAM = re.compile(r"cheat|hack|aimbot|download|free robux|free v-?bucks|mod menu|injector|giveaway", re.I)   # never a theme
KIND_W = {"youtube": 1.0, "live": 0.75, "reddit": 0.75, "news": 0.5}   # our audience lives on Shorts; press coverage counts less


def topics(game, rows, searches=(), trend=None, n=5, others=()):
    """[{topic, videos, mentions, views, src}] best first. rows = this week's shorts [{title, channel, weight}] plus
    news/reddit headlines (kind news|reddit, weight 0). Rank = distinct mentions + share of YouTube views."""
    gtok, bad = _game_tokens(game), leak_terms(game)
    chans = {re.sub(r"[^a-z0-9]", "", (r.get("channel") or "").lower().split(":", 1)[-1]) for r in rows if r.get("kind", "youtube") in ("youtube", "live")} - {""}
    skip = _STOP | _HYPE | _FOREIGN

    def ok(ph):
        w = ph.split()
        if w[0] in skip or w[-1] in skip or w[0] in _STOP or w[-1] in _STOP:
            return False
        if all(x in gtok or x in skip or x.isdigit() for x in w):
            return False
        if any(x in gtok for x in w) and len(w) == 1:
            return False
        k = ph.replace(" ", "")
        if k in others or any(b in ph for b in bad) or OFFICIAL.search(ph) or SPAM.search(ph):
            return False
        return len(k) > 2

    mention, views, kinds, yt = {}, {}, {}, {}
    for r in rows:
        kind = r.get("kind", "youtube")
        w = [x for x in _words(r["title"]) if x not in gtok]   # "gta 6 trailer 3" -> "trailer 3"
        seen = set()
        for size in (1, 2, 3):
            for i in range(len(w) - size + 1):
                ph = " ".join(w[i:i + size])
                if ph not in seen and ok(ph):
                    seen.add(ph)
        for ph in seen:
            mention.setdefault(ph, {})[r.get("channel") or r["title"]] = KIND_W.get(kind, 1.0)   # one per channel / outlet / post
            views[ph] = views.get(ph, 0) + r.get("weight", 0)
            kinds.setdefault(ph, set()).add(kind)
            yt[ph] = yt.get(ph, 0) + (kind == "youtube")
    # A creator's name is a theme only when OTHER channels talk about them ("xQc reacts to..." on clip channels):
    # the audience-hijack angle of the 2026-11-23 study. Their own uploads do not count.
    for ph in list(mention):
        k = ph.replace(" ", "")
        if k in chans:
            mention[ph] = {c: w for c, w in mention[ph].items() if re.sub(r"[^a-z0-9]", "", c.lower().split(":", 1)[-1]) != k}
            kinds[ph].add("criador")
    # Search signals: tails after the game's name ("gta 6 map" -> "map")
    sig = {}
    for label, qs in (("busca", searches), ("trends", (trend or {}).get("rising", []) + (trend or {}).get("top", []))):
        for q in clean(list(qs), game):
            tail = " ".join(x for x in _words(q) if x not in gtok)
            if tail and ok(tail):
                sig.setdefault(tail, set()).add(label)

    def hits(ph):
        return {lab for t, labs in sig.items() for lab in labs if f" {ph} " in f" {t} " or f" {t} " in f" {ph} "}

    top_v = max(views.values(), default=0) or 1
    # A single word must come from 3+ different sources, a phrase from 2+ (one channel repeating itself is not a theme).
    cand = {ph: (sum(m.values()) + 3 * views[ph] / top_v) * (1 + 0.25 * len(hits(ph)))
            for ph, m in mention.items() if len(m) >= (3 if len(ph.split()) == 1 and "criador" not in kinds[ph] else 2)}
    # Prefer the longer phrase when it keeps most of the shorter one's weight ("trailer 3" over "trailer").
    for ph in sorted(cand, key=lambda p: -len(p.split())):
        for sub in list(cand):
            if sub != ph and f" {sub} " in f" {ph} " and ph in cand and cand[ph] >= 0.6 * cand[sub]:
                cand.pop(sub, None)
    out = []
    for ph, sc in sorted(cand.items(), key=lambda kv: -kv[1]):
        if any(f" {ph} " in f" {o['topic']} " or f" {o['topic']} " in f" {ph} " for o in out):
            continue
        out.append({"topic": ph, "videos": yt[ph], "mentions": len(mention[ph]), "views": int(views[ph]),
                    "src": sorted(kinds[ph] | hits(ph))})
        if len(out) == n:
            break
    for t, labs in sig.items():   # nothing repeated on YouTube yet: fall back to what people search
        if len(out) >= 3:
            break
        if not any(f" {t} " in f" {o['topic']} " or f" {o['topic']} " in f" {t} " for o in out):
            out.append({"topic": t, "videos": 0, "mentions": 0, "views": 0, "src": sorted(labs)})
    return out


def _foreign(title, own, others):
    t = re.sub(r"[^a-z0-9]", "", title.lower())
    return any(o in t for o in others) and not any(a in t for a in own)


def analyse(game, others=()):
    rows, this_week = youtube(game)
    lives = live(game)
    # YouTube search for "GTA 5 shorts" returns mostly GTA 6 videos (found 2026-11-23): a title that names another
    # game of the radar and not this one is about that other game, so it counts for neither views nor themes.
    own, keep, spill = aliases(game), [], []
    for r in rows:
        (spill if _foreign(r["title"], own, others) else keep).append(r)
    rows = keep
    med = statistics.median([r["views"] for r in rows]) if rows else 0
    ordered = sorted((r["views"] for r in rows), reverse=True)
    cap = max(ordered[1] if len(ordered) > 1 else (ordered[0] if ordered else 1), 1)  # the single biggest video counts as the 2nd
    styles, classified = {}, 0
    for r in rows:
        r["style"] = style_of(r["title"])
        r["official"] = bool(OFFICIAL.search(r["channel"] or ""))
        r["weight"] = min(r["views"], cap)
        if r["style"] != "outro" and not r["official"]:
            styles[r["style"]] = styles.get(r["style"], 0) + r["weight"]
            classified += r["weight"]
    # Reach prior from the 2026-11-16 and 2026-11-18 studies: side-by-side comparisons cap at 10-60k even on big channels,
    # official-scene hype and quick news are what we can make legally that travels furthest.
    ranked = sorted(styles.items(), key=lambda kv: -kv[1] * REACH.get(kv[0], 1.0))
    creators = [r for r in rows if not r["official"]]
    return {"game": game, "videos": len(rows), "views": int(sum(sorted((r["weight"] for r in rows), reverse=True)[:10])), "raw_views": sum(r["views"] for r in rows),
            "this_week": this_week, "median_views": int(med),
            "median_seconds": round(statistics.median([r["dur"] for r in rows])) if rows else 0,
            "styles": [{"key": k, "share": round(100 * v / classified), "views": int(v)} for k, v in ranked[:3]] if classified else [],
            "top": sorted(creators or rows, key=lambda r: -r["views"])[:6],
            "live_viewers": sum(x["viewers"] for x in lives if not _foreign(x["title"], own, others)),
            "lives": sum(1 for x in lives if not _foreign(x["title"], own, others)),
            "_rows": [{"title": r["title"], "channel": r["channel"], "weight": r["weight"]} for r in rows] + news_titles(game)
                     + [{"title": x["title"], "channel": "live:" + x["channel"], "weight": 0, "kind": "live"} for x in lives],
            "_spill": [{"title": r["title"], "channel": r["channel"], "weight": r["views"]} for r in spill],
            "searches": clean(suggest(game), game)}


def seo(game, top, trend=None):
    """Tags and hashtags the week's most viewed videos of this game use, weighted by views."""
    def meta(r):
        out = run(["yt-dlp", f"https://www.youtube.com/watch?v={r['id']}", "-J", "--skip-download", "--no-warnings"],
                  check=False, timeout=60).stdout
        try:
            d = json.loads(out)
            return r["views"], d.get("tags") or [], re.findall(r"#\w+", (d.get("description") or "") + " " + d.get("title", ""))
        except ValueError:
            return r["views"], [], re.findall(r"#\w+", r["title"])

    tags, hashes = {}, {}
    with ThreadPoolExecutor(6) as ex:
        for views, t, h in ex.map(meta, top):
            for x in {y.strip().lower() for y in t if 2 < len(y) < 40}:
                tags[x] = tags.get(x, 0) + views
            for x in {y.lower() for y in h if len(y) > 3 and not y[1:].isdigit()}:
                hashes[x] = hashes.get(x, 0) + views
    # Keep only tags about the game or gaming: other creators' names and their own genre tags (skit, vine, comedy...)
    # in our metadata would be misleading (found 2026-11-18: "jaden williams", "#skit" leaked in from a sketch channel).
    names = {re.sub(r"[^a-z0-9]", "", (r.get("channel") or "").lower()) for r in top} - {""}
    gtok = {w for w in re.findall(r"[a-z0-9]+", game.lower()) if len(w) > 1 and not w.isdigit()}
    GAMING = re.compile(r"gam(e|ing|er)|ps5|xbox|pc\b|trailer|gameplay|update|release|news|leonida|vice ?city", re.I)

    def relevant(t):
        k = re.sub(r"[^a-z0-9]", "", t.lower())
        if any(n and (n in k or k in n) for n in names if len(k) > 3):
            return False
        return any(w in t.lower() for w in gtok) or bool(GAMING.search(t))

    tags = {t: v for t, v in tags.items() if relevant(t)}
    hashes = {h: v for h, v in hashes.items() if relevant(h[1:])}
    slug = "#" + re.sub(r"[^a-z0-9]", "", game.lower())
    top_hash = clean([h for h, _ in sorted(hashes.items(), key=lambda kv: -kv[1])
                      if h not in ("#shorts", slug) and not OFFICIAL_TAG.match(h[1:])], game)
    rising = [q for q in (trend or {}).get("rising", []) if game.lower().split()[0] in q.lower()]
    searches = list(dict.fromkeys(rising + clean(suggest(game), game)))
    hidden, size = [], -2
    for t in clean([game.lower()] + searches + [t for t, _ in sorted(tags.items(), key=lambda kv: -kv[1])], game):
        if t not in hidden and not OFFICIAL_TAG.match(t.replace(" ", "")) and size + len(t) + 2 <= 495:
            hidden.append(t)   # YouTube limit is 500 chars, counted as joined with ", "
            size += len(t) + 2
    return {"game": game, "searches": searches,
            "hashtags_youtube": list(dict.fromkeys([slug, "#shorts"] + top_hash[:3])),
            "hashtags_tiktok": list(dict.fromkeys([slug] + top_hash[:3] + ["#gaming", "#gamingtiktok", "#fyp"])),
            "hidden_tags": hidden,
            "title_examples": clean([r["title"][:90] for r in top[:4]], game)}


def gtrends(game):
    """Google Trends, YouTube search, USA, last 7 days (trendspyg, plain HTTP, no Chrome, no key).
    momentum = last 48 h vs the 5 days before, in %; rising = queries growing fastest. None if Google refuses."""
    try:
        import trendspyg
        e = trendspyg.download_google_trends_explore(game, geo="US", timeframe="now 7-d", gprop="youtube",
                                                    include_geo=False, engine="http", max_retries=2)
    except Exception:
        return None
    pts = [p["value"] for p in e.get("interest_over_time") or [] if not p.get("is_partial")]
    momentum = None
    if len(pts) > 60:
        old, new = statistics.mean(pts[:-48]), statistics.mean(pts[-48:])
        momentum = round(100 * (new - old) / old) if old else None
    rq = e.get("related_queries") or {}
    rising = clean([r["query"] for r in rq.get("rising", [])], game)
    return {"momentum": momentum, "rising": rising[:8],
            "rising_label": {r["query"]: r.get("formatted_value", "") for r in rq.get("rising", [])},
            "top": clean([r["query"] for r in rq.get("top", [])], game)[:8]}


WEEK_ANY = "CAMSAggD"   # sorted by view count + this week, any length (gameplay VODs and streams)
RISK = "sem permissão do dono; usuário assumiu o risco em 2026-11-23"


def creators(game, limit=30, min_subs=1_000_000, others=()):
    """Biggest creators who played the game this week (user law 2026-11-23: every cut research surveys them and the
    cut comes from one of them). [{channel, subs, views, id, url, title, dur, lang}] by subscribers, one video each
    (their most viewed). Publishers/media and other-game titles are dropped."""
    ids = run(["yt-dlp", f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(game)}&sp={WEEK_ANY}",
               "--flat-playlist", "--playlist-end", str(limit), "--no-warnings", "--print", "%(id)s"],
              check=False, timeout=120).stdout.split()

    def meta(vid):
        out = run(["yt-dlp", "-J", "--skip-download", "--no-warnings", f"https://www.youtube.com/watch?v={vid}"],
                  check=False, timeout=90).stdout
        try:
            d = json.loads(out)
        except ValueError:
            return None
        return {"channel": d.get("channel") or "", "subs": d.get("channel_follower_count") or 0,
                "views": d.get("view_count") or 0, "id": vid, "url": f"https://www.youtube.com/watch?v={vid}",
                "title": d.get("title") or "", "dur": d.get("duration") or 0, "lang": (d.get("language") or "")[:2],
                "desc": (d.get("description") or "")[:600]}

    own, best = aliases(game), {}
    with ThreadPoolExecutor(6) as ex:
        for m in ex.map(meta, ids):
            if not m or m["subs"] < min_subs or OFFICIAL.search(m["channel"]) or _foreign(m["title"], own, others):
                continue
            if m["channel"] not in best or m["views"] > best[m["channel"]]["views"]:
                best[m["channel"]] = m
    return sorted(best.values(), key=lambda m: -m["subs"])


def cut_pick(game, crs, topics_):
    """The cut to make now: the creator video that talks about the hottest theme (title/description), English first,
    then the biggest creator. Returns the pick with the pipeline command (risk recorded per the 2026-11-23 law)."""
    if not crs:
        return None
    words = [t["topic"] for t in topics_ or [] if t.get("topic")]

    def score(m):
        text = m["title"].lower()   # a title about the theme; the description mentions everything (2026-11-23)
        hit = next((i for i, w in enumerate(words) if w in text), None)
        return (hit is None, hit if hit is not None else 99, m["lang"] != "en", -m["subs"])

    # The biggest first (user: "os maiores streamers"): the 5 biggest English creators, the theme only breaks ties.
    pool = sorted([m for m in crs if m["lang"] == "en"] or crs, key=lambda m: -m["subs"])[:5]
    m = min(pool, key=score)
    theme = next((w for w in words if w in m["title"].lower()), words[0] if words else "")   # else: find it inside the VOD
    return {"game": game, "creator": m["channel"], "subs": m["subs"], "views": m["views"], "title": m["title"],
            "url": m["url"], "theme": theme,
            "command": (f'python pipeline.py --clip-url "{m["url"]}" --highlights --clips 1 --game <id> '
                        f'--credit "{m["channel"]}" --permission "{RISK}" --visual manual')}


def games_to_scan():
    names = list(read_json(WATCHLIST, None) or DEFAULT_WATCH)
    for p in GAMES.glob("*.json"):
        if not p.stem.startswith("_"):
            names.append(read_json(p).get("name", p.stem))
    steam = steam_top()
    names += [n for n, _ in steam]
    seen, out = set(), []
    for n in names:
        k = re.sub(r"[^a-z0-9]", "", n.lower())
        if k and k not in seen:
            seen.add(k)
            out.append(n)
    return out[:13], dict(steam)


def build(refresh=False, focus=None):
    cached = read_json(PANEL, None)
    if cached and not refresh and not focus and time.time() - cached.get("at", 0) < 3 * 3600:
        return cached
    names, steam = games_to_scan()
    al = {n: aliases(n) for n in names}
    with ThreadPoolExecutor(5) as ex:
        games = [g for g in ex.map(lambda n: analyse(n, set().union(*(v for k, v in al.items() if k != n)) - al[n]), names) if g["videos"]]
    # Games whose search had no Short from this week fell back to all-time results: their views are not "this week",
    # so they go after every game with fresh data (found 2026-11-23: Minecraft "150 mi" was all-time).
    # Heat = this week's Shorts views + people watching it live now, each as a share of the leader (2026-11-23).
    top_v = max((g["views"] for g in games), default=0) or 1
    top_l = max((g.get("live_viewers", 0) for g in games), default=0) or 1
    for g in games:
        g["heat"] = round(100 * (0.6 * g["views"] / top_v + 0.4 * g.get("live_viewers", 0) / top_l))
    games.sort(key=lambda g: (not g["this_week"], -g["heat"]))
    for g in games:
        g["steam_players"] = steam.get(g["game"], 0)
    for g in games[:7]:   # one by one: Google throttles parallel requests
        g["trend"] = gtrends(g["game"])
    for g in games:   # reddit one by one too (anonymous rate limit); only games with a profile have subreddits
        oth = set().union(*(v for k, v in al.items() if k != g["game"])) - al[g["game"]]
        g["_rows"] = [r for r in g["_rows"] + reddit_titles(g["game"]) if not _foreign(r["title"], al[g["game"]], oth)]
    for g in games:   # a GTA 6 video found by the "GTA 5" search still counts for GTA 6's themes
        for r in g.pop("_spill"):
            t = re.sub(r"[^a-z0-9]", "", r["title"].lower())
            for h in games:
                if h is not g and any(a in t for a in al[h["game"]]) and all(x["title"] != r["title"] for x in h["_rows"]):
                    cap = max((x["weight"] for x in h["_rows"]), default=0) or r["weight"]   # same cap as h's own videos
                    h["_rows"].append({**r, "weight": min(r["weight"], cap)})
    for g in games:
        others = {re.sub(r"[^a-z0-9]", "", x["game"].lower()) for x in games if x is not g}
        g["topics"] = topics(g["game"], g.pop("_rows"), g.get("searches", []), g.get("trend"), others=others)
    # Recommendation: the (game, style) pair with the most creator views this week, unless a game is asked for.
    with_style = [g for g in games if g["styles"] and g["this_week"]] or [g for g in games if g["styles"]]
    best = max(with_style, key=lambda g: g["styles"][0]["views"]) if with_style else (games[0] if games else None)
    lead = next((g for g in games if focus and focus.lower() in g["game"].lower()), best) if games else None
    pick = None
    if lead and lead["styles"]:
        key = lead["styles"][0]["key"]
        label = next(l for k, l, _ in STYLES if k == key)
        pick = {"game": lead["game"], "style": key, "label": label, "share": lead["styles"][0]["share"],
                "command": HOW[key][0], "how": HOW[key][1], "seconds": lead["median_seconds"],
                "topic": (lead.get("topics") or [{}])[0].get("topic")}
    data = {"at": time.time(), "when": time.strftime("%Y-%m-%d %H:%M"), "games": games, "recommend": pick,
            "seo": seo(lead["game"], lead["top"], lead.get("trend")) if lead else None,
            "rising": (lead.get("trend") or {}).get("rising", []) if lead else [],
            "package": None,
            "note": "Views: Shorts mais vistos da semana na busca do YouTube. Ao vivo: soma de quem assiste as 20 maiores lives do jogo agora. Tendência ▲▼: Google Trends (busca no YouTube, EUA, 48 h vs 5 dias antes). "
                    "TikTok não tem fonte aberta: hashtags do TikTok são derivadas."}
    # Biggest creators of the top 2 games and the cut to make (user law, 2026-11-23).
    for g in games[:2]:
        oth = set().union(*(v for k, v in al.items() if k != g["game"])) - al[g["game"]]
        g["creators"] = creators(g["game"], others=oth)
    if lead and "creators" not in lead:
        lead["creators"] = creators(lead["game"], others=set().union(*(v for k, v in al.items() if k != lead["game"])) - al[lead["game"]])
    data["cut"] = cut_pick(lead["game"], lead.get("creators") or [], lead.get("topics")) if lead else None
    if data["seo"] and pick:
        data["package"] = package(pick["game"], pick["style"], data["seo"]["searches"], data["seo"]["hashtags_youtube"], pick.get("topic"))
    write_json(PANEL, data)
    WIDGET.write_text(widget(data), encoding="utf-8")
    return data


def _n(v):
    return f"{v / 1e6:.1f} mi".replace(".", ",") if v >= 1e6 else (f"{v / 1e3:.0f} mil" if v >= 1e3 else str(v))


def _mom(g):
    m = (g.get("trend") or {}).get("momentum")
    if m is None:
        return ""
    return f' · <span style="color:var({"--text-success" if m > 5 else ("--text-danger" if m < -5 else "--text-secondary")})">{"▲" if m > 5 else ("▼" if m < -5 else "■")} {m:+d}%</span>'


def _cut_html(d):
    """Card of the cut to make now + the biggest creators of the lead game (user law 2026-11-23)."""
    c = d.get("cut")
    if not c:
        return ""
    e = html.escape
    js = lambda t: e(json.dumps(t, ensure_ascii=False), quote=True)
    lead = next((g for g in d["games"] if g["game"] == c["game"]), {})
    chips = "".join(f'<span class="pc">{e(m["channel"])} · {_n(m["subs"])}</span>' for m in (lead.get("creators") or [])[:10])
    ask = f'/cortes-games cortar {c["creator"]} ({c["url"]}) sobre: {c["theme"]}'
    return (f'<div class="pr"><span class="pt">Corte de streamer agora</span>'
            f'<p style="font-size:16px;font-weight:500;margin:10px 0 2px">{e(c["creator"])} · {e(c["game"])}</p>'
            f'<p style="font-size:14px;margin:0 0 4px">Tema: <b>{e(c["theme"] or "-")}</b> · {_n(c["subs"])} inscritos · {_n(c["views"])} views</p>'
            f'<p class="ps" style="margin:0 0 10px">{e(c["title"][:90])}</p>'
            f'<button onclick="sendPrompt({js(ask)})">Fazer este corte ↗</button>'
            f'<p class="pm" style="margin-top:10px">Maiores criadores no jogo esta semana</p>{chips}</div>')


def widget(d):
    """Compact HTML for the Claude panel. Buttons call sendPrompt(), so a click is the user's next message."""
    e = html.escape
    js = lambda t: e(json.dumps(t, ensure_ascii=False), quote=True)
    top = max((g["views"] for g in d["games"]), default=1)
    rows = ""
    for g in d["games"][:7]:
        style = next((l for k, l, _ in STYLES if g["styles"] and k == g["styles"][0]["key"]), "sem padrão claro")
        rows += (f'<div class="pg"><span class="pn">{e(g["game"])}</span><span><span class="pb" style="width:{max(3, round(100 * g["views"] / top))}%"></span>'
                 f'<span class="ps">{_n(g["views"])} views{"" if g["this_week"] else " (sem Shorts da semana: total antigo)"} · {_n(g.get("live_viewers", 0))} vendo ao vivo ({g.get("lives", 0)} lives) · {e(style)}{_mom(g)}</span></span>'
                 f'<button onclick="sendPrompt({js("/cortes-games fazer um clipe de " + g["game"])})">Clipe ↗</button></div>')
        if g.get("topics"):
            rows += ('<div class="pt2"><span class="pm" style="margin:0 6px 0 0">Temas mais citados (nº de menções):</span>'
                     + "".join(f'<button class="pc pk" title="{t["mentions"]} menções ({t["videos"]} Shorts) · {_n(t["views"])} views · {e(", ".join(t["src"]))}" '
                               f'onclick="sendPrompt({js("/cortes-games fazer um clipe de " + g["game"] + " sobre: " + t["topic"])})">'
                               f'{'<i class="ti ti-flame" aria-hidden="true"></i> ' if i == 0 else ""}{e(t["topic"])} <span class="pm">{t["mentions"] or "busca"}'
                               f'{" · ▲" if "trends" in t["src"] else ""}</span></button>'
                               for i, t in enumerate(g["topics"])) + '</div>')
    r, s_ = d.get("recommend"), d.get("seo")
    rec = ""
    if r:
        rec = (f'<div class="pr"><span class="pt">Recomendado agora</span>'
               f'<p style="font-size:16px;font-weight:500;margin:10px 0 2px">{e(r["label"])} de {e(r["game"])}</p>'
               + (f'<p style="font-size:14px;margin:0 0 4px">Tema mais em alta: <b>{e(r["topic"])}</b></p>' if r.get("topic") else "")
               + f'<p class="ps" style="margin:0 0 10px">{r["share"]}% das views de criadores com estilo identificado, nesta semana. Duração típica: {r["seconds"]} s. {e(r["how"])}</p>'
               f'<button onclick="sendPrompt({js("/cortes-games fazer: " + r["label"] + " de " + r["game"])})">Fazer este clipe ↗</button></div>')
        if REACH.get(r["style"], 1.0) < 1.0:
            alt = "/cortes-games fazer uma notícia relâmpago de " + r["game"] + " (modelo TmarTn2)"
            rec += (f'<p class="ps" style="margin:0 0 10px;color:var(--text-danger)">Atenção: nos estudos de 16 e 18/11 este estilo tem teto baixo '
                    f'(10 a 60 mil, mesmo em canal grande). Na dúvida, prefira notícia relâmpago ou cena oficial "looks INSANE".</p>'
                    f'<button onclick="sendPrompt({js(alt)})" style="margin-bottom:12px">Fazer notícia relâmpago ↗</button>')
        pb = PLAYBOOK.get(r["style"])
        if pb:
            cell = lambda k, v: f'<div><p class="pm" style="margin:0">{e(k)}</p><p style="font-size:13px;margin:2px 0 0">{e(v)}</p></div>'
            rec += (f'<p class="ph" style="margin-top:0">Playbook do clipador: {e(r["label"])} (estudo dos top 20 clipadores, 18/11)</p>'
                    '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px 14px;margin-bottom:8px">'
                    + cell("Shorts", pb["shorts"]) + cell("TikTok", pb["tiktok"]) + cell("Tomada", pb["shot"]) + cell("Voz", pb["voice"])
                    + cell("Música", pb["music"]) + cell("Gancho (0-3 s)", pb["hook"]) + cell("Final", pb["end"]) + cell("Referência", pb["ref"])
                    + '</div><p class="pm">Sempre</p>' + "".join(f'<span class="pc">{e(x)}</span>' for x in ALWAYS))
    acts = [("ti-news", "Tema ou notícia", "/tema"), ("ti-cut", "Cortar vídeo por URL", "/cortar-video"),
            ("ti-bolt", "Notícia relâmpago", "/cortes-games fazer uma notícia relâmpago (modelo TmarTn2)"),
            ("ti-list-numbers", "Lista / top N", "/cortes-games fazer uma lista top N"),
            ("ti-columns", "Comparação lado a lado", "/cortes-games fazer uma comparação lado a lado"),
            ("ti-language", "Adaptar vídeo gringo", "/traduzir-url"), ("ti-flame", "Auto-hype", "/auto-hype 1"),
            ("ti-movie", "Minerar B-roll", "/minerar-broll"), ("ti-tags", "Pacote de postagem", "/cortes-games montar o pacote de postagem do último vídeo"),
            ("ti-refresh", "Atualizar radar", "/cortes-games atualizar o radar do painel")]
    buttons = "".join(f'<button style="text-align:left" onclick="sendPrompt({js(p_)})"><i class="ti {i}" style="font-size:16px;vertical-align:-2px;margin-right:6px" aria-hidden="true"></i>{e(t)} ↗</button>'
                      for i, t, p_ in acts)
    seo_html = ""
    if s_:
        chip = lambda xs: "".join(f'<span class="pc">{e(x)}</span>' for x in xs)
        seo_html = (f'<p class="ph">Hashtags e buscas em alta: {e(s_["game"])}</p>'
                    f'<p class="pm">YouTube</p>{chip(s_["hashtags_youtube"])}'
                    f'<p class="pm">TikTok (derivadas do YouTube)</p>{chip(s_["hashtags_tiktok"])}'
                    + (f'<p class="pm">Subindo agora no Google Trends (YouTube, EUA, 7 dias)</p>{chip(d["rising"][:8])}' if d.get("rising") else "")
                    + f'<p class="pm">O que buscam (autocomplete do YouTube)</p>{chip([x for x in s_["searches"] if x not in (d.get("rising") or [])][:6])}'
                    f'<p class="pm">Tags ocultas do YouTube ({len(", ".join(s_["hidden_tags"]))}/500 caracteres)</p>'
                    f'<p class="ps" style="margin:0">{e(", ".join(s_["hidden_tags"]))}</p>')
    pk = d.get("package")
    if pk:
        tl = "".join(f'<p style="font-size:14px;margin:4px 0">{e(t["title"])} <span class="pm">· nota {t["score"]}/100</span></p>'
                     for t in pk["titles"])
        seo_html = (f'<p class="ph">Título e descrição recomendados (em inglês, já linted)</p>{tl}'
                    f'<p class="pm">Descrição</p><p class="ps" style="margin:0;white-space:pre-line">{e(pk["description"])}</p>' + seo_html)
    css = ('<style>.pg{display:grid;grid-template-columns:minmax(0,150px) minmax(0,1fr) auto;gap:10px;align-items:center;padding:6px 0}'
           '.pt2{padding:0 0 8px;display:flex;flex-wrap:wrap;align-items:center}.pk{border:0;cursor:pointer;font-size:12px}'
           '.pg button{font-size:13px}.pn{font-size:14px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}'
           '.pb{display:block;height:8px;border-radius:4px;background:var(--fill-accent)}.ps{font-size:13px;color:var(--text-secondary)}'
           '.ph{font-size:13px;color:var(--text-secondary);margin:1.25rem 0 6px}.pm{font-size:12px;color:var(--text-muted);margin:6px 0 2px}'
           '.pc{font-size:12px;background:var(--surface-1);border-radius:var(--radius);padding:3px 8px;margin:0 4px 4px 0;display:inline-block}'
           '.pr{border:2px solid var(--border-accent);border-radius:12px;padding:1rem 1.25rem;margin:0 0 1.25rem;background:var(--surface-2)}'
           '.pt{background:var(--bg-accent);color:var(--text-accent);font-size:12px;padding:4px 12px;border-radius:var(--radius)}</style>')
    return (css + '<h2 class="sr-only">Painel de clipes: jogos em alta no YouTube, estilo recomendado e ações disponíveis.</h2>'
            f'<div style="padding:0.5rem 0">{_cut_html(d)}{rec}<p class="ph" style="margin-top:0">Jogos em alta (Shorts mais vistos da semana + quem assiste ao vivo agora, YouTube)</p>{rows}'
            f'<p class="ph">O que você quer fazer</p>'
            f'<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:8px">{buttons}</div>{seo_html}'
            f'<p class="pm" style="margin-top:1rem">Radar de {e(d["when"])}. {e(d["note"])}</p></div>')


def seo_pack(game_name):
    """SEO block for the posting package of a game, from the cached panel (None when the cache is about another game)."""
    d = read_json(PANEL, None) or {}
    s = d.get("seo")
    if s and re.sub(r"\W", "", s["game"].lower()) == re.sub(r"\W", "", game_name.lower()):
        return s
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--game", help="put this game's recommendation and SEO in the panel")
    a = ap.parse_args()
    d = build(a.refresh, a.game)
    for g in d["games"][:8]:
        st = ", ".join(f"{x['key']} {x['share']}%" for x in g["styles"]) or "-"
        m = (g.get("trend") or {}).get("momentum")
        say(f"{g['game'][:26]:26} {_n(g['views']):>9} views{'' if g['this_week'] else '*'} | {_n(g.get('live_viewers', 0)):>8} ao vivo | calor {g.get('heat', 0):3} | {g['videos']:2} vídeos | mediana {g['median_seconds']} s | trends {'-' if m is None else f'{m:+d}%'} | {st}")
        if g.get("topics"):
            say("   temas: " + " · ".join(f"{t['topic']} ({t['mentions']} menções: {'+'.join(t['src'])}, {_n(t['views'])} views)" for t in g["topics"]))
    for g in d["games"]:
        if g.get("creators"):
            say(f"maiores criadores em {g['game']}: " + " · ".join(f"{m['channel']} {_n(m['subs'])}" for m in g["creators"][:8]))
    if d.get("cut"):
        c = d["cut"]
        say(f"CORTE: {c['creator']} ({_n(c['subs'])}) sobre '{c['theme']}' -> {c['url']} | {c['title'][:60]}")
    if d.get("recommend"):
        r = d["recommend"]
        say(f"recomendado: {r['label']} de {r['game']} ({r['share']}% das views), tema: {r.get('topic') or '-'} -> {r['command']}")
    if d.get("seo"):
        say(f"hashtags YT: {' '.join(d['seo']['hashtags_youtube'])} | TikTok: {' '.join(d['seo']['hashtags_tiktok'])}")
        say(f"tags ocultas: {len(', '.join(d['seo']['hidden_tags']))} chars")
    if d.get("package"):
        for t in d["package"]["titles"]:
            say(f"título {t['score']:3}/100: {t['title']}")
    say(f"painel: {WIDGET} ({WIDGET.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
