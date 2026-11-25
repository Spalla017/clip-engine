"""Mode 2 brain: free Gemini Flash API or a local Ollama model. Every call returns parsed JSON.

Mode 1 (Claude Code as director) never calls this module: Claude writes the same JSON files and passes
them with --script-file / --segments-file.
"""
import json
import os
import re
import urllib.error
import urllib.request

from .common import PLATFORMS, ROOT, load_env

# Checked 2026-11-13 with the free keys: 3.8-flash and flash-latest answer 429 (no free quota), 2.5-flash 404
# (retired). 3.5-flash works (sometimes slow), 3.5-flash-lite is the fast fallback.
GEMINI_MODELS = ["gemini-3.5-flash", "gemini-3.5-flash-lite"]
LANG = "en"  # set by pipeline.py --lang


def _out():
    """Output-language order appended to every writing prompt (the prompt body itself is in Portuguese)."""
    from .common import LANGS
    L = LANGS[LANG]
    return (f"\n\nIDIOMA DE SAÍDA OBRIGATÓRIO: {L['name']}. Todo texto que vai para o vídeo ou para a postagem "
            f"(title, headlines, hooks, script, description, hook_line1/2, lines) deve estar nesse idioma, escrito "
            f"como nativo, para {L['audience']}. As tags de cena continuam exatamente como na lista permitida.")


class NoBrain(RuntimeError):
    pass


def _post(url, body, headers, timeout=75):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _parse(text):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    return json.loads(text)


def ask_json(prompt, images=None, temperature=0.8):
    """Send one prompt, get JSON back. Order: Gemini keys x models, then Ollama."""
    load_env()
    errors = []
    keys = [os.environ[k] for k in ("GEMINI_API_KEY", "GEMINI_API_KEY_2", "GEMINI_API_KEY_3", "GOOGLE_API_KEY") if os.environ.get(k)]
    models = ([os.environ["GEMINI_MODEL"]] if os.environ.get("GEMINI_MODEL") else []) + GEMINI_MODELS
    parts = [{"text": prompt}]
    for img in images or []:
        import base64
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(open(img, "rb").read()).decode()}})
    quota = False
    for model in models:
        if quota:
            break
        for key in keys:
            try:
                data = _post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                    {"contents": [{"parts": parts}],
                     "generationConfig": {"response_mime_type": "application/json", "temperature": temperature}},
                    {"x-goog-api-key": key})
                return _parse(data["candidates"][0]["content"]["parts"][0]["text"])
            except urllib.error.HTTPError as e:
                errors.append(f"{model}: HTTP {e.code}")
                if e.code == 429:  # cota esgotada: falha rapida na 1a tentativa, sem rodar o resto de chaves x modelos
                    errors.append("Gemini 429 (cota): abortando tentativas")
                    quota = True
                    break
                if e.code in (404, 400):  # model gone or request refused: other keys will not help
                    break
            except Exception as e:  # malformed JSON, timeout, empty candidate
                errors.append(f"{model}: {type(e).__name__}")
    ollama = os.environ.get("OLLAMA_MODEL")
    if ollama and not images:
        try:
            data = _post(os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434") + "/api/generate",
                         {"model": ollama, "prompt": prompt, "format": "json", "stream": False}, {}, timeout=600)
            return _parse(data["response"])
        except Exception as e:
            errors.append(f"ollama: {type(e).__name__}")
    raise NoBrain("No LLM available (set GEMINI_API_KEY in clip-engine/.env or OLLAMA_MODEL). "
                  "In Claude Code, write the JSON yourself and pass --script-file / --segments-file. "
                  + "; ".join(errors[-4:]))


def _ref(name):
    path = ROOT / "references" / name
    return path.read_text(encoding="utf-8") if path.exists() else ""


SCRIPT_SHAPE = """Responda só com JSON:
{"title": "título do vídeo (até 70 caracteres, honesto)",
 "headlines": ["manchete fixa do topo, até 5 palavras, CAIXA ALTA", "alternativa 2", "alternativa 3"],
 "hooks": [["linha 1 do gancho (4-8 palavras)", "linha 2 (3-6 palavras)"], ["alt 1", "alt 2"], ["alt 1", "alt 2"]],
 "script": "texto corrido da narração, sem marcações, sem emojis",
 "tags": ["tag de cena para cada bloco do roteiro, na ordem, escolhidas da lista permitida"],
 "description": "descrição curta com 1 pergunta para comentário",
 "sources": ["URL ou nome da fonte de cada fato"]}"""


def write_script(topic, game, platform="shorts", context="", longer=False):
    from .common import LANGS
    wps = LANGS[LANG]["wps"]
    lo, hi = PLATFORMS[platform]["script_seconds"]
    words = (int(lo * wps), int(hi * wps))
    if longer:
        words = (words[0] + 25, words[1] + 35)
    prompt = f"""Você é roteirista de um canal brasileiro de cortes e notícias de jogos. Jogo: {game['name']}.
Tema: {topic}
Hoje é {__import__('time').strftime('%Y-%m-%d')}. Situação atual confirmada do jogo (vale mais que a sua memória):
{game.get('known_facts', '(sem dados)')}
Contexto pesquisado (use só fatos daqui ou da situação atual acima; nada inventado; se o contexto for só um título, fale do que o título afirma e do que é oficial, sem inventar detalhes de imagem):
{context or '(sem contexto extra)'}

Regras:
- Linguagem falada, frases curtas. Entre {words[0]} e {words[1]} palavras (narração de {lo}-{hi} s).
- Gancho nos 3 primeiros segundos: lacuna de curiosidade + fato específico (número, nome, data).
- Loop: a última frase é COMPLETA (nunca cortada no meio) e devolve o espectador à primeira frase, por exemplo com uma pergunta que a abertura responde. Ela fecha o assunto do vídeo, não muda de assunto.
- Trate rumor como rumor ("a comunidade acredita", "segundo o trailer"). Nunca prometa o que o vídeo não entrega.
- Proibido: palavrões, descrição de violência gráfica, e qualquer conteúdo vindo de vazamento ou build roubada.
  Notas da editora: {game.get('notes', '')}
- Rótulos honestos para manchete: {', '.join(game.get('honest_labels', []))}.
- Tags de cena permitidas: {', '.join(game.get('footage_tags', []))}. A primeira tag deve ser uma destas (abertura calma): {', '.join(game.get('safe_opening_tags', []))}.

Guia de ganchos do canal:
{_ref('ganchos.md')[:1800]}

{SCRIPT_SHAPE}""" + _out()
    return ask_json(prompt)


def translate_batch(segments, game, batch_size=20):
    """Translate transcript segments to the output language, 20 per call (Youtube-clipper-skill technique)."""
    from .common import LANGS
    target = LANGS[LANG]["name"]
    out = []
    for i in range(0, len(segments), batch_size):
        batch = segments[i:i + batch_size]
        numbered = "\n".join(f"{j}: {s['text']}" for j, s in enumerate(batch))
        data = ask_json(f"""Traduza para {target}, natural e falado, as falas numeradas de um vídeo sobre {game['name']}.
Mantenha nomes próprios. Responda só com JSON: {{"lines": ["tradução 0", "tradução 1", ...]}} com exatamente {len(batch)} itens.

{numbered}""", temperature=0.3)
        lines = data.get("lines", [])
        for j, s in enumerate(batch):
            out.append({**s, "tx": lines[j] if j < len(lines) else s["text"]})
    return out


def rewrite_with_commentary(translated_text, game, source_credit, platform="shorts"):
    """Turn a translated source into our own analysis (reused-content policy: add real commentary)."""
    lo, hi = PLATFORMS[platform]["script_seconds"]
    prompt = f"""Abaixo está a tradução do que foi dito num vídeo de {source_credit} sobre {game['name']}.
Escreva um roteiro NOSSO, de {int(lo * 2.9)}-{int(hi * 2.9)} palavras, que:
- cite a fonte pelo nome uma vez ("o canal {source_credit} mostrou que…");
- resuma o ponto central com palavras próprias (não é dublagem frase a frase);
- acrescente análise inédita: o que isso significa para o jogador, comparação com jogos anteriores, o que ainda não se sabe;
- não acrescente número, tamanho, preço ou data que não esteja na tradução;
- siga as mesmas regras de gancho de 3 s, última frase completa que devolve à abertura, sem palavrões, sem violência gráfica, sem vazamentos.
Tags de cena permitidas: {', '.join(game.get('footage_tags', []))}.

Tradução:
{translated_text[:6000]}

{SCRIPT_SHAPE}""" + _out()
    return ask_json(prompt)


def score_segments(transcript, game, clips=3):
    """Pick the best cuts of a long video: 5 rubric dimensions + mandatory safety dimension."""
    lines = "\n".join(f"[{s['start']:.1f}-{s['end']:.1f}] {s['text']}" for s in transcript["segments"])
    prompt = f"""Escolha os {clips} melhores cortes (20-60 s cada, de blocos de assunto diferentes) desta transcrição de um vídeo sobre {game['name']}.

Rubrica (0-100 cada): hook 0.30, coherence 0.25, emotion 0.20, value 0.15, payoff 0.10.
{_ref('scoring-rubric.md')[:2500]}

6ª dimensão obrigatória, "safety" (0-100): 100 = limpo. Zere e descarte o trecho se tiver discurso de ódio,
palavrão pesado ou descrição de violência gráfica nos primeiros 7 s, ou se falar de material vazado/build roubada.
Só entram cortes com safety >= 80 e nota final >= 60.

Responda só com JSON:
{{"segments": [{{"id": 1, "start": 0.0, "end": 0.0, "hook_line1": "4-8 palavras", "hook_line2": "3-6 palavras",
  "title": "título", "scores": {{"hook": 0, "coherence": 0, "emotion": 0, "value": 0, "payoff": 0, "safety": 0}},
  "final_score": 0, "why": "1 frase"}}]}}

Transcrição:
{lines[:60000]}""" + _out()
    return ask_json(prompt, temperature=0.4)


def vision_audit(sheet_jpg, game):
    """Post-render visual check of the contact sheet (frames at 0.5, 3.0, 6.5 s, 25, 50, 90 %)."""
    return ask_json(f"""Esta folha tem 6 quadros de um vídeo vertical sobre {game['name']}, na ordem: 0.5 s, 3.0 s, 6.5 s, 25 %, 50 %, 90 %.
Responda só com JSON: {{"third_party_watermark": false, "old_burned_captions": false, "graphic_first_7s": false,
"captions_readable_in_safe_zone": true, "notes": "o que você viu"}}
Camadas NOSSAS, que nunca contam como defeito: texto grande de gancho no topo nos 2 primeiros quadros (linha branca
e linha azul), pílula de manchete no topo depois disso, legenda palavra a palavra em caixa alta com uma palavra
amarela, card escuro com citação, círculo de lupa com aro colorido, crédito pequeno "Source: ...", barra fina de progresso.
Placas, letreiros e outdoors do mundo do jogo também não contam.
- third_party_watermark: logo do TikTok, CapCut, Reels, Kwai ou marca d'água de outro canal em qualquer quadro.
- old_burned_captions: uma SEGUNDA legenda de fala, diferente da nossa, queimada no vídeo de origem (duas legendas ao mesmo tempo).
- graphic_first_7s: sangue, ferimento explícito, tortura ou massacre nos 3 primeiros quadros.
- captions_readable_in_safe_zone: nossas legendas legíveis, fora dos 200 px do topo e dos 350 px da base.""",
                    images=[sheet_jpg], temperature=0.1)
