"""Fast self-test of the engine's logic. No render, no GPU, no network: about 10 seconds.

  python -m src.selfcheck

Run it after any change to pipeline.py / src/ and before a production day. It covers the rules that broke
in real runs: repeated shots, hallucinated captions, leak wording, trailer audio, own-gameplay clips.
"""
import importlib
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

from .common import ENGINE, WORK, load_game, localize, say

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


def run():
    pipeline = importlib.import_module("pipeline")
    from . import compliance_guard as guard
    from . import highlights, voice_sync
    from .voicestudio_client import _tts_text, split_sentences

    game = localize(load_game("gta6"), "en")

    # --- voice text handling
    parts = split_sentences("Number one: Vice City. The neon capital of Leonida, with beaches, towers and traffic "
                            "that never sleeps. Number two: the Keys.")
    check("split_sentences merges short takes", all(len(p.split()) >= 5 for p in parts), str([len(p.split()) for p in parts]))
    check("tts text drops final full stop", _tts_text("Port Gellhorn.") == "Port Gellhorn")

    # --- captions follow the script; hallucinated tail is dropped
    script = "GTA 6 has six regions. Pick one."
    heard = [{"text": " " + w, "startMs": i * 300, "endMs": i * 300 + 250} for i, w in
             enumerate("GTA six has six regions pick one thank you".split())]
    caps = voice_sync.align_to_script(heard, script.replace("GTA 6", "GTA six"))
    check("align drops hallucinated tail", [c["text"].strip() for c in caps][-1] == "one.", str([c["text"] for c in caps][-3:]))

    # --- enumerations become cards with anchored tags
    s2 = "Number one: Vice City. Big. Number two: the Leonida Keys. Islands."
    words = s2.split()
    caps2 = [{"text": " " + w, "startMs": i * 400, "endMs": i * 400 + 350} for i, w in enumerate(words)]
    items = pipeline.list_items(s2, caps2, game)
    check("list cards detected", [i["label"] for i in items] == ["VICE CITY", "LEONIDA KEYS"], str(items))
    check("list card tags", [i["tag"] for i in items] == ["cidade", "keys"])

    # --- b-roll: no repeated shot, anchors honoured
    from . import asset_miner
    lib = asset_miner.library("gta6")
    if lib:
        picked = pipeline.pick_broll("gta6", None, 40, anchors=[(10.0, "keys")])
        shots = [(c["meta"].get("source_url"), c["meta"].get("shot")) for c in picked]
        check("no repeated shot", len(shots) == len(set(shots)))
        t, at = 0.0, None
        for c in picked:
            if c["meta"]["tag"] == "keys":
                at = t
            t += max(2, round(c["dur"] * 60) - 1) / 60
        check("anchored clip near its cue", at is not None and abs(at - 10.0) <= 1.6, f"at={at}")
        check("fallback never opens with excluded tag", picked[0]["meta"]["tag"] in game["safe_opening_tags"], picked[0]["meta"]["tag"])
    else:
        check("footage library present", False, "assets/footage/gta6 is empty")

    # --- compliance gate
    plan = {"flow": "topic", "title": "t", "headline": "LEAKED GAMEPLAY", "hook": ["a", "b"], "script": "A calm look at the city."}
    rep, plan = guard.preflight(plan, game)
    check("leak headline replaced", plan["headline"] in game["honest_labels"], plan["headline"])
    rep, _ = guard.preflight({"flow": "topic", "headline": "X", "hook": ["a", ""], "script": "This leaked build shows it."}, game)
    check("leak in script is flagged", "leak_wording" in rep["failed"])
    rep, _ = guard.preflight({"flow": "clip", "headline": "X", "hook": ["a", ""], "script": "hello there friend", "captions": [],
                              "credit": "Source: someone", "main": {"path": "x", "kind": "official_trailer_audio", "volume": 1}}, game)
    check("trailer audio cut is blocked", "clip_source_music" in rep["failed"])
    rep, _ = guard.preflight({"flow": "clip", "headline": "", "hook": ["", ""], "pov": "POV: first hour", "script": "", "captions": [],
                              "credit": "", "main": {"path": "x", "kind": "own_gameplay", "volume": 1}}, game)
    check("own gameplay passes without credit or captions", rep["status"] == "APPROVED", str(rep["failed"]))
    rep, _ = guard.preflight({"flow": "clip", "headline": "X", "hook": ["a", ""], "script": "hello", "captions": [], "credit": "",
                              "main": {"path": "x", "kind": "commentary_source", "volume": 1}}, game)
    check("creator clip needs credit", "source_credit" in rep["failed"])
    check("creator clip needs permission", "creator_permission" in rep["failed"])
    rep, _ = guard.preflight({"flow": "clip", "headline": "X", "hook": ["a", ""], "script": "", "captions": [], "no_speech": True,
                              "credit": "Source: someone", "permission": "licença do vídeo de origem: Creative Commons Attribution",
                              "main": {"path": "x", "kind": "commentary_source", "volume": 1}}, game)
    check("CC-licensed creator clip passes", rep["status"] == "APPROVED", str(rep["failed"]))
    rep, _ = guard.preflight({"flow": "topic", "headline": "X", "hook": ["a", ""], "script": "ok fine",
                              "music": {"path": "m.mp3", "kind": "unknown"}}, game)
    check("unlicensed music is blocked", "music_license" in rep["failed"])

    rep, _ = guard.preflight({"flow": "compare", "headline": "X", "hook": ["a", ""], "script": "ok fine", "captions": [],
                              "compare_kinds": ["official_trailer", "licensed"], "credit": "", "permission": ""}, game)
    check("compare with licensed clip needs credit + licence", {"source_credit", "creator_permission"} <= set(rep["failed"]), str(rep["failed"]))

    # --- trend panel: leak words and publisher names never reach tags; titles linted
    from . import trend_panel as tp
    check("panel drops leak tags", tp.clean(["gta 6 leak", "gta 6 vazamento", "gta 6 map"], "GTA 6") == ["gta 6 map"])
    check("panel drops publisher hashtags", bool(tp.OFFICIAL_TAG.match("ign")) and not tp.OFFICIAL_TAG.match("gaming"))
    pk = tp.package("GTA 6", "comparacao", ["gta 6 map"], ["#gta6", "#shorts"])
    check("panel package has 3 titles + question", len(pk["titles"]) == 3 and pk["question"] in pk["description"])
    tps = tp.topics("GTA 6", [{"title": "GTA 6 Trailer 3 is INSANE #shorts", "channel": "A", "weight": 900},
                              {"title": "gta 6 trailer 3 breakdown", "channel": "B", "weight": 500},
                              {"title": "GTA 6 leak map", "channel": "C", "weight": 800},
                              {"title": "GTA 6 leak map again", "channel": "D", "weight": 800}], ["gta 6 trailer 3 date"])
    tps2 = tp.topics("GTA 6", [{"title": "xQc LOSES IT in GTA 6", "channel": "xQc Clips", "weight": 500},
                               {"title": "xQc finds the radio in GTA 6", "channel": "Clipper B", "weight": 400},
                               {"title": "my GTA 6 stream", "channel": "xQc", "weight": 900}])
    cp = tp.cut_pick("GTA 6", [{"channel": "Big", "subs": 9e7, "views": 1, "title": "GTA 6 part 1", "desc": "", "lang": "hi", "url": "u1"},
                               {"channel": "Mid", "subs": 5e6, "views": 1, "title": "GTA 6 radio is insane", "desc": "", "lang": "en", "url": "u2"}],
                     [{"topic": "radio"}])
    check("panel cut: picks the creator video about the hot theme, risk recorded", cp["creator"] == "Mid" and cp["theme"] == "radio"
          and "assumiu o risco" in cp["command"], str(cp))
    import pipeline as pl
    cc = pl.clean_caps([{"text": " shit,", "startMs": 0, "endMs": 9000}])
    check("captions: swear words censored and a word never shows > 700 ms", cc[0]["text"] == " s***," and cc[0]["endMs"] == 700, str(cc))
    from . import asset_miner as am
    check("miner: reaction/compilation uploads are never a licensed source",
          bool(am.NOT_THEIRS.search("The GTA 6 Backlash - Asmongold, xQc on GTA")) and not am.NOT_THEIRS.search("GTA 6 - Robbing a bank"))
    check("panel topics: a creator named by other channels is a theme (criador)",
          any(t["topic"] == "xqc" and "criador" in t["src"] for t in tps2) and tp.style_of("IShowSpeed reacts to GTA 6") == "streamer", str(tps2))
    check("panel topics: repeated theme first, game name/leak never a theme",
          bool(tps) and tps[0]["topic"] == "trailer 3" and not any("leak" in t["topic"] or "gta" in t["topic"] for t in tps), str(tps))

    # --- highlights on a synthetic recording: quiet, one loud burst at 40 s
    tmp = WORK / "_selfcheck.wav"
    sr = 8000
    x = np.random.default_rng(0).standard_normal(sr * 70) * 0.01
    x[sr * 40: sr * 43] += np.random.default_rng(1).standard_normal(sr * 3) * 0.5
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())
    found = highlights.find(tmp, clips=1, seconds=20)
    tmp.unlink(missing_ok=True)
    check("highlight window contains the burst", bool(found) and found[0]["start"] <= 40 <= found[0]["end"], str(found))

    # --- profiles and Remotion types
    for gid in ("gta6", "zelda-oot"):
        g = localize(load_game(gid), "en")
        check(f"profile {gid} localized", isinstance(g.get("say_as"), dict) and isinstance(g.get("honest_labels"), list))
    tsc = subprocess.run("npx tsc --noEmit", shell=True, cwd=str(ENGINE), capture_output=True, text=True)
    check("remotion types compile", tsc.returncode == 0, (tsc.stdout or tsc.stderr)[-200:])


def main():
    try:
        run()
    except Exception as e:  # a crash is a failed check, not a silent pass
        check("selfcheck ran to the end", False, f"{type(e).__name__}: {e}")
    bad = [r for r in RESULTS if not r[1]]
    for name, ok, detail in RESULTS:
        if not ok:
            say(f"FAIL {name} {detail}")
    say(f"selfcheck: {len(RESULTS) - len(bad)}/{len(RESULTS)} ok" + ("" if not bad else " - " + ", ".join(b[0] for b in bad)))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    main()
