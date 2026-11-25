// Overlays from the user's 7 model Shorts (references/estilo-modelos-2026-11-18.md):
// 2-3 word captions in the middle of the frame (1-word until 2026-11-18), stickers (game logo, cover, emoji, cut-out, short text),
// a pulsing green arrow and a fixed "CONFIRMED BY ..." badge.
import React from "react";
import { Img, interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import type { Arrow, Badge, Caption, Sticker } from "../types";
import { H, W } from "../types";

const FUNC = new Set(["a", "an", "the", "of", "to", "in", "on", "at", "for", "and", "or", "is", "it", "its", "my", "your",
  "his", "her", "by", "with", "from", "as", "be", "was", "are", "this", "that", "no", "so", "but", "i", "you", "he", "we"]);
const clean = (s: string) => s.toLowerCase().replace(/[^a-z0-9$%']/g, "");
const NUM = /[0-9$%]/;
export const WORD_Y = 1180; // centre of the caption line (61 % of 1920), measured on the model Shorts

interface Word { text: string; color: string }
interface Group { words: Word[]; startMs: number; endMs: number }

const END = /[.!?]["”)]?$/;
const MAXC = 18; // characters per block: 3 short words, 2 long ones
const strip = (t: string) => t.trim().replace(/^["“(]+|[.,!?;:"”)]+$/g, "");

// Blocks of 2-3 words (user rule 2026-11-18: no lone connective like "BUT", "HAS", "CAME" on screen).
// An acronym and its number are one unit ("GTA 6"). A block closes at 2 words unless both are function words,
// at 3, at a sentence end or a long pause. A lone word is merged into a neighbour in the same sentence; it may
// stand alone only when it is a whole sentence ("Missions?").
// Caption timing rules (user, 2026-11-23, Speed cut): a word never shows longer than MAX_WORD_MS, and any gap without
// speech longer than GAP_MS hides the caption completely (no block may span it, no block is held over it).
const MAX_WORD_MS = 700;
const GAP_MS = 600;
const groups = (capsIn: Caption[], emphasis: string[], accentNum: string): Group[] => {
  const caps = capsIn.map((c) => ({ ...c, endMs: Math.min(c.endMs, c.startMs + MAX_WORD_MS) }));
  const gap = (a: Caption[], b: Caption[]) => b[0].startMs - a[a.length - 1].endMs;
  const em = new Set(emphasis.map(clean));
  const units: Caption[][] = [];
  for (let i = 0; i < caps.length; i++) {
    const c = caps[i], n = caps[i + 1];
    if (n && /^[A-Z]{2,4}$/.test(c.text.trim()) && /^\d{1,2}('s)?[.,!?]?$/.test(n.text.trim())) { units.push([c, n]); i++; }
    else units.push([c]);
  }
  const raw: Caption[][] = [];
  let cur: Caption[] = [];
  const flush = () => { if (cur.length) { raw.push(cur); cur = []; } };
  const len = (xs: Caption[]) => xs.map((x) => strip(x.text)).join(" ").length;
  units.forEach((u, k) => {
    const next = units[k + 1];
    if (cur.length && (u[0].startMs - cur[cur.length - 1].endMs > GAP_MS ||
        (cur.length >= 2 && (cur.length + u.length > 3 || len([...cur, ...u]) > MAXC)))) flush();
    cur = cur.concat(u);
    const lastText = u[u.length - 1].text.trim();
    if (END.test(lastText) || !next) return flush();
    if (/[,;:]$/.test(lastText)) { // a comma is a natural break; a lone word before it joins the previous block
      const prev = raw[raw.length - 1];
      if (cur.length === 1 && prev && !END.test(prev[prev.length - 1].text.trim()) && prev.length < 3 && gap(prev, cur) <= GAP_MS &&
          len([...prev, ...cur]) <= MAXC + 4) { prev.push(...cur); cur = []; return; }
      if (cur.length >= 2) return flush();
    }
    const endsFunc = FUNC.has(clean(cur[cur.length - 1].text)); // never end a block on "of", "and", "the"
    if (cur.length >= 3 || (cur.length === 2 && !endsFunc)) flush();
  });
  // merge lone words into a neighbour of the same sentence
  const ends = (g: Caption[]) => END.test(g[g.length - 1].text.trim());
  for (let i = 0; i < raw.length; i++) {
    const g = raw[i];
    if (g.length !== 1) continue;
    const prev = raw[i - 1], next = raw[i + 1];
    const pair = (x: Caption[]) => x.length >= 2 && /^[A-Z]{2,4}$/.test(x[x.length - 2].text.trim()) && /^\d/.test(x[x.length - 1].text.trim());
    if (prev && !ends(prev) && gap(prev, g) <= GAP_MS && len([...prev, ...g]) <= MAXC + 4) { prev.push(...g); raw.splice(i--, 1); }
    else if (prev && !ends(prev) && prev.length === 3 && !pair(prev) && gap(prev, g) <= GAP_MS) { g.unshift(prev.pop() as Caption); }
    else if (next && !ends(g) && next.length <= 2 && gap(g, next) <= GAP_MS) { next.unshift(...g); raw.splice(i--, 1); }
  }
  const out: Group[] = raw.map((g) => ({
    words: g.map((p) => ({ text: strip(p.text),
      color: NUM.test(p.text) ? accentNum : em.has(clean(p.text)) ? "#FFD400" : "#FFFFFF" })),
    startMs: g[0].startMs, endMs: g[g.length - 1].endMs,
  }));
  // Hold each block until the next one starts (short gaps only), so the screen never blinks between words.
  return out.map((g, i) => {
    const next = out[i + 1];
    return { ...g, endMs: next && next.startMs - g.endMs <= GAP_MS ? next.startMs : g.endMs + 250 };
  });
};

export const WordCaptions: React.FC<{ captions: Caption[]; emphasis?: string[] }> = ({ captions, emphasis = [] }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const ms = (frame / fps) * 1000;
  const gs = React.useMemo(() => groups(captions, emphasis, "#FF3EA5"), [captions, emphasis]);
  const g = gs.find((x) => ms >= x.startMs && ms < x.endMs);
  if (!g) return null;
  const local = frame - Math.floor((g.startMs / 1000) * fps);
  const s = spring({ frame: local, fps, config: { mass: 0.6, damping: 11, stiffness: 260 } });
  const scale = 0.72 + 0.28 * s;
  const chars = g.words.map((w) => w.text).join(" ").length;
  // Montserrat 800 caps + stroke is ~0.8 em per character: keep the block on one line inside 980 px
  const size = Math.round(Math.min(104, 940 / Math.max(1, chars * 0.8)));
  return (
    <div style={{ position: "absolute", left: 50, right: 50, top: WORD_Y - size * 0.6, display: "flex", justifyContent: "center" }}>
      <span
        style={{
          fontFamily: "'Montserrat', sans-serif",
          fontWeight: 800,
          fontSize: size,
          lineHeight: 1.1,
          textTransform: "uppercase",
          textAlign: "center",
          whiteSpace: "nowrap",
          WebkitTextStroke: `${Math.round(size * 0.13)}px #000`,
          paintOrder: "stroke fill",
          textShadow: "0 8px 18px rgba(0,0,0,0.55)",
          transform: `scale(${scale})`,
          display: "inline-block",
        }}
      >
        {g.words.map((w, i) => (
          <span key={i} style={{ color: w.color }}>{(i ? " " : "") + w.text}</span>
        ))}
      </span>
    </div>
  );
};

/** Image or text that pops in at a point of the frame (x, y = centre, 0..1) and pops out at the end. */
export const Stickers: React.FC<{ items?: Sticker[] }> = ({ items = [] }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps;
  return (
    <>
      {items.filter((s) => t >= s.at && t < s.at + s.dur).map((s, i) => {
        const local = frame - Math.round(s.at * fps);
        const end = Math.round(s.dur * fps);
        let scale = 1;
        let rot = s.rot ?? 0;
        if ((s.anim ?? "pop") === "pop") {
          const inS = spring({ frame: local, fps, config: { mass: 0.7, damping: 9, stiffness: 220 } });
          const out = interpolate(local, [end - 5, end], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
          scale = inS * (s.dur > 0.6 ? out : 1);
          rot = (s.rot ?? 0) + Math.sin(local / 7) * 2.5; // the model stickers wobble a little while on screen
        }
        const style: React.CSSProperties = {
          position: "absolute", left: s.x * W, top: s.y * H, transform: `translate(-50%, -50%) scale(${scale}) rotate(${rot}deg)`,
        };
        return s.src ? (
          <Img key={i} src={s.src} style={{ ...style, width: s.w, height: "auto",
            filter: s.shadow ? "drop-shadow(0 10px 18px rgba(0,0,0,0.6))" : undefined }} />
        ) : (
          <div key={i} style={{ ...style, width: "max-content", whiteSpace: "pre", textAlign: "center", fontFamily: "'Montserrat', sans-serif", fontWeight: 800,
            fontSize: s.w, lineHeight: 1.05, textTransform: "uppercase", color: s.color,
            WebkitTextStroke: `${Math.round(s.w * 0.12)}px #000`, paintOrder: "stroke fill", textShadow: "0 6px 14px rgba(0,0,0,0.5)" }}>
            {s.text}
          </div>
        );
      })}
    </>
  );
};

/** Chunky green arrow; the tip sits on (x, y) and it bobs along its own axis. angle 90 = pointing down. */
export const Arrows: React.FC<{ items?: Arrow[] }> = ({ items = [] }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps;
  return (
    <>
      {items.filter((a) => t >= a.at && t < a.at + a.dur).map((a, i) => {
        const local = frame - Math.round(a.at * fps);
        const pop = spring({ frame: local, fps, config: { mass: 0.6, damping: 10, stiffness: 240 } });
        const bob = Math.sin((local / fps) * Math.PI * 4) * 16; // 2 pulses per second
        const L = a.len ?? 230;
        return (
          <div key={i} style={{ position: "absolute", left: a.x * W, top: a.y * H, width: 0, height: 0,
            transform: `rotate(${a.angle ?? 90}deg)` }}>
            {/* drawn pointing +x with the tip at the origin */}
            <svg width={L + 40} height={L * 0.8} viewBox={`${-L - 20} ${-L * 0.4} ${L + 40} ${L * 0.8}`}
              style={{ position: "absolute", left: -L - 20 + bob * -1, top: -L * 0.4, overflow: "visible",
                transform: `scale(${pop})`, transformOrigin: `${L + 20}px ${L * 0.4}px`,
                filter: "drop-shadow(0 8px 12px rgba(0,0,0,0.55))" }}>
              <polygon
                points={`0,0 ${-L * 0.42},${-L * 0.3} ${-L * 0.42},${-L * 0.12} ${-L},${-L * 0.12} ${-L},${L * 0.12} ${-L * 0.42},${L * 0.12} ${-L * 0.42},${L * 0.3}`}
                fill={a.color ?? "#39FF14"} stroke="#000" strokeWidth={10} strokeLinejoin="round" />
            </svg>
          </div>
        );
      })}
    </>
  );
};

/** Dark pill with a green check at the top ("CONFIRMED BY ROCKSTAR"), only for official facts. */
export const TopBadge: React.FC<{ badge?: Badge; total: number }> = ({ badge, total }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  if (!badge || !badge.text) return null;
  const t = frame / fps;
  const to = badge.to > 0 ? badge.to : total / fps;
  if (t < badge.from || t >= to) return null;
  const s = spring({ frame: frame - Math.round(badge.from * fps), fps, config: { damping: 14, stiffness: 180 } });
  return (
    <div style={{ position: "absolute", top: badge.top, left: 0, width: W, display: "flex", justifyContent: "center",
      transform: `translateY(${(1 - s) * -40}px)`, opacity: s }}>
      <div style={{ display: "flex", alignItems: "center", gap: 14, padding: "12px 26px 12px 14px", borderRadius: 40,
        background: "rgba(12,12,16,0.86)", border: "2px solid rgba(255,255,255,0.18)" }}>
        <div style={{ width: 40, height: 40, borderRadius: 20, background: "#22C55E", color: "#fff", display: "flex",
          alignItems: "center", justifyContent: "center", fontSize: 28, fontWeight: 900, fontFamily: "'Montserrat', sans-serif" }}>✓</div>
        <span style={{ fontFamily: "'Montserrat', sans-serif", fontWeight: 800, fontSize: 32, color: "#fff", letterSpacing: 1,
          textTransform: "uppercase" }}>{badge.text}</span>
      </div>
    </div>
  );
};
