import React from "react";
import { Easing, Img, Sequence, interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import type { InfoCard } from "../types";

/**
 * Context card in the middle of the frame: the picture of what the voice names (country flag, city photo)
 * with the date and time in big accent type next to it. The gameplay keeps running behind it.
 */
const InfoCardView: React.FC<{ card: InfoCard; accent: string; top: number }> = ({ card, accent, top }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const frames = Math.max(18, Math.round(card.dur * fps));
  const pop = spring({ frame, fps, config: { mass: 0.9, damping: 13, stiffness: 210 } });
  const textIn = spring({ frame: frame - Math.round(fps * 0.12), fps, config: { mass: 0.8, damping: 14, stiffness: 220 } });
  const out = interpolate(frame, [frames - fps * 0.22, frames], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const drift = interpolate(frame, [0, frames], [1, 1.07], { easing: Easing.inOut(Easing.quad) });
  const flag = card.kind === "flag";
  const imgW = flag ? 600 : 700;
  const imgH = flag ? 310 : 440;
  const stroke = "-4px -4px 0 #000, 4px -4px 0 #000, -4px 4px 0 #000, 4px 4px 0 #000, 0 8px 18px rgba(0,0,0,0.6)";
  // Fit date + time on ONE line inside ~940 px (2026-11-20: "SAN ANDREAS · 2004" + "NEVER FOUND" wrapped onto 3 lines
  // and ran into the caption). Bangers is ~0.48 em per glyph.
  const chars = (card.date || "").length + (card.time || "").length;
  const big = Math.round(Math.min(((card.time || card.date || "").length > 12 ? 92 : 112) * (card.src ? 1 : 1.25),
    (940 - (card.date && card.time ? 26 : 0)) / (0.48 * Math.max(1, chars))));
  return (
    <div style={{ position: "absolute", left: 0, width: 1080, top: card.src ? top : top + 200, display: "flex", flexDirection: "column", alignItems: "center", opacity: out }}>
      {/* soft dim so the card reads over bright gameplay without hiding it */}
      <div style={{ position: "absolute", top: -60, width: 980, height: imgH + 420, borderRadius: 60,
        background: "radial-gradient(closest-side, rgba(0,0,0,0.45), rgba(0,0,0,0))", opacity: pop }} />
      {card.src ? (
        <div style={{ width: imgW, height: imgH, borderRadius: 28, overflow: "hidden", border: "6px solid #fff",
          boxShadow: "0 22px 50px rgba(0,0,0,0.6)", transform: `scale(${0.6 + 0.4 * pop}) rotate(${(1 - pop) * -6}deg)` }}>
          <Img src={card.src} style={{ width: "100%", height: "100%", objectFit: "cover", transform: `scale(${drift})` }} />
        </div>
      ) : null}
      {card.title ? (
        <div style={{ marginTop: 22, fontFamily: "'Montserrat', sans-serif", fontWeight: 800, fontSize: 50, letterSpacing: 2, whiteSpace: "nowrap",
          color: "#fff", textTransform: "uppercase", textShadow: stroke, opacity: textIn,
          transform: `translateY(${(1 - textIn) * 40}px)` }}>{card.title}</div>
      ) : null}
      {card.date || card.time ? (
        <div style={{ marginTop: 6, display: "flex", alignItems: "baseline", gap: 26, whiteSpace: "nowrap", fontFamily: "'Bangers', sans-serif",
          fontSize: big, lineHeight: 1, letterSpacing: 3, textShadow: stroke, opacity: textIn,
          transform: `translateY(${(1 - textIn) * 60}px) scale(${0.85 + 0.15 * textIn})` }}>
          {card.date ? <span style={{ color: "#fff" }}>{card.date}</span> : null}
          {card.time ? <span style={{ color: accent }}>{card.time}</span> : null}
        </div>
      ) : null}
    </div>
  );
};

export const InfoCards: React.FC<{ cards: InfoCard[]; accent: string; top: number }> = ({ cards, accent, top }) => {
  const { fps } = useVideoConfig();
  return (
    <>
      {cards.map((c, i) => (
        <Sequence key={i} from={Math.round(c.at * fps)} durationInFrames={Math.max(18, Math.round(c.dur * fps))}>
          <InfoCardView card={c} accent={accent} top={top} />
        </Sequence>
      ))}
    </>
  );
};
