import React from "react";
import { Easing, Sequence, interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import type { ListCard, UiCard } from "../types";

const PANEL: React.CSSProperties = {
  background: "rgba(10,8,22,0.86)",
  borderRadius: 30,
  padding: "26px 34px",
  fontFamily: "'Montserrat', sans-serif",
  color: "#fff",
};

const fmt = (n: number) => Math.round(n).toLocaleString("en-US");

/** One game-UI card: meter, running counter or ticking checklist. Pops in, fades out. */
const UiCardView: React.FC<{ card: UiCard; accent: string }> = ({ card, accent }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const frames = Math.max(18, Math.round(card.dur * fps));
  const pop = spring({ frame, fps, config: { mass: 0.8, damping: 13, stiffness: 220 } });
  const out = interpolate(frame, [frames - fps * 0.2, frames], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  // the value moves during the first 60 % of the card, then holds so it can be read
  const run = interpolate(frame, [fps * 0.25, Math.max(fps * 0.3, frames * 0.6)], [0, 1], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.out(Easing.cubic) });
  const edge = { border: `4px solid ${accent}`, boxShadow: `0 0 34px ${accent}99, 0 22px 50px rgba(0,0,0,0.55)` };
  let body: React.ReactNode = null;
  if (card.kind === "bar") {
    const v = Math.max(0, Math.min(1, card.value)) * run;
    body = (
      <div style={{ width: 760 }}>
        <div style={{ height: 34, borderRadius: 17, background: "rgba(255,255,255,0.16)", overflow: "hidden" }}>
          <div style={{ width: `${v * 100}%`, height: "100%", borderRadius: 17,
            background: `linear-gradient(90deg, #4FD1FF, ${accent})` }} />
        </div>
        <div style={{ display: "flex", justifyContent: "space-between", marginTop: 12, fontSize: 30, fontWeight: 700,
          textTransform: "uppercase", letterSpacing: 2, color: "rgba(255,255,255,0.85)" }}>
          <span>{card.left}</span><span>{card.right}</span>
        </div>
      </div>
    );
  } else if (card.kind === "counter") {
    const n = card.from + (card.to - card.from) * run;
    const down = card.to < card.from;
    body = (
      <div style={{ fontFamily: "'Bangers', sans-serif", fontSize: 150, lineHeight: 1, letterSpacing: 4,
        color: run >= 1 && down ? "#FF4D4D" : "#fff", textShadow: "0 6px 0 #000" }}>
        {card.prefix}{fmt(n)}{card.suffix}
      </div>
    );
  } else {
    const per = Math.max(fps * 0.35, (frames * 0.7) / Math.max(1, card.items.length));
    body = (
      <div style={{ display: "grid", gap: 14, minWidth: 620 }}>
        {card.items.map((it, i) => {
          const tick = card.itemAt?.[i] != null ? Math.round(card.itemAt[i] * fps) : Math.round(fps * 0.3 + i * per);
          const on = spring({ frame: frame - tick, fps, config: { damping: 12, stiffness: 240 } });
          return (
            <div key={i} style={{ display: "flex", alignItems: "center", gap: 20, fontSize: 40, fontWeight: 800,
              textTransform: "uppercase", opacity: 0.45 + 0.55 * on }}>
              <span style={{ width: 52, height: 52, borderRadius: 14, border: `4px solid ${accent}`, display: "flex",
                alignItems: "center", justifyContent: "center", background: on > 0.5 ? accent : "transparent",
                transform: `scale(${0.8 + 0.2 * on})`, fontSize: 34, color: "#000" }}>{on > 0.5 ? "✓" : ""}</span>
              <span>{it}</span>
            </div>
          );
        })}
      </div>
    );
  }
  return (
    <div style={{ position: "absolute", left: 0, width: 1080, display: "flex", justifyContent: "center", opacity: out }}>
      <div style={{ ...PANEL, ...edge, transformOrigin: card.y != null ? "50% 0%" : "50% 50%",
        transform: `scale(${(0.7 + 0.3 * pop) * (card.scale ?? 1)}) translateY(${(1 - pop) * 40}px)`,
        display: "flex", flexDirection: "column", alignItems: "center", gap: 16 }}>
        {card.title ? (
          <div style={{ fontSize: 34, fontWeight: 800, letterSpacing: 3, textTransform: "uppercase", color: accent }}>{card.title}</div>
        ) : null}
        {body}
      </div>
    </div>
  );
};

export const UiCards: React.FC<{ cards: UiCard[]; accent: string; top: number }> = ({ cards, accent, top }) => {
  const { fps } = useVideoConfig();
  return (
    <div style={{ position: "absolute", top, left: 0, width: 1080 }}>
      {cards.map((c, i) => (
        <Sequence key={i} from={Math.round(c.at * fps)} durationInFrames={Math.max(18, Math.round(c.dur * fps))} layout="none">
          <div style={{ position: "absolute", top: c.y != null ? c.y - top : 0, left: 0, width: 1080 }}>
            <UiCardView card={c} accent={accent} />
          </div>
        </Sequence>
      ))}
    </div>
  );
};

/** Round badge with the current list number. Appears at the first list item and stays; bounces on every change. */
export const ListBadge: React.FC<{ cards: ListCard[]; accent: string; top: number; left?: number }> = ({ cards, accent, top, left = 60 }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps;
  const cur = [...cards].filter((c) => c.at <= t).pop();
  if (!cur) return null;
  const s = spring({ frame: frame - Math.round(cur.at * fps), fps, config: { mass: 0.7, damping: 10, stiffness: 260 } });
  const label = String(parseInt(cur.index, 10) || cur.index);
  return (
    <div style={{ position: "absolute", top, left, width: 128, height: 128, borderRadius: 64, display: "flex",
      alignItems: "center", justifyContent: "center", background: "rgba(10,8,22,0.9)", border: `6px solid ${accent}`,
      boxShadow: `0 0 30px ${accent}aa`, transform: `scale(${0.6 + 0.4 * s})`,
      fontFamily: "'Bangers', sans-serif", fontSize: 84, color: "#fff", lineHeight: 1 }}>
      {label}
    </div>
  );
};
