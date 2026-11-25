import React from "react";
import { AbsoluteFill, Easing, Img, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { z } from "zod";
import { fontFaceCSS } from "./styles/fonts";

/**
 * Map-infographic beat (style of the user's reference n07mLsWNyyE, 2026-11-19): the game map fills the 9:16 frame,
 * a neon route draws from the previous find to the next one with an icon riding it, the camera pushes in on the new
 * pin and a black counter card names the find. Rendered alone into a short silent clip that the pipeline uses as B-roll.
 */
export const MapPinSchema = z.object({
  map: z.string(),                       // file in public/
  mapW: z.number(), mapH: z.number(),    // pixel size of the map image
  pins: z.array(z.object({ x: z.number(), y: z.number(), icon: z.string().optional() })), // 0..1 of the map image
  active: z.number(),                    // index of the pin this beat lands on
  label: z.string(), region: z.string().optional().default(""),
  accent: z.string().optional().default("#FF3EA5"),
  route: z.string().optional().default("#E8FF3B"),
  zoom: z.number().optional().default(2.1),
  // zoom at the start of the push; > 1 also pins the map top to the frame top (no dark strip above it, 2026-11-20)
  minZoom: z.number().optional().default(1),
  durationInSeconds: z.number().optional().default(2.6),
});
export type MapPinProps = z.infer<typeof MapPinSchema>;

const W = 1080, H = 1920;

export const MapPin: React.FC<MapPinProps> = (p) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  const k = fps / 60;
  // map fitted to the frame width, centred vertically on the ocean background
  const base = Math.max(W / p.mapW, (H * 0.82) / p.mapH);
  const mw = p.mapW * base, mh = p.mapH * base;
  const ox = (W - mw) / 2, oy = (H - mh) / 2 + 60;
  const P = (i: number) => ({ x: ox + p.pins[i].x * mw, y: oy + p.pins[i].y * mh });
  const cur = P(p.active);
  const prev = p.active > 0 ? P(p.active - 1) : null;

  // route: quadratic curve prev -> cur, drawn over frames 6..48
  const draw = interpolate(frame, [6 * k, 48 * k], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.inOut(Easing.cubic) });
  const cx = prev ? (prev.x + cur.x) / 2 + (cur.y - prev.y) * 0.18 : 0;
  const cy = prev ? (prev.y + cur.y) / 2 - (cur.x - prev.x) * 0.18 : 0;
  const q = (t: number) => prev ? {
    x: (1 - t) ** 2 * prev.x + 2 * (1 - t) * t * cx + t * t * cur.x,
    y: (1 - t) ** 2 * prev.y + 2 * (1 - t) * t * cy + t * t * cur.y } : cur;
  const rider = q(draw);
  const path = prev ? `M ${prev.x} ${prev.y} Q ${cx} ${cy} ${cur.x} ${cur.y}` : "";
  const len = prev ? Math.hypot(cur.x - prev.x, cur.y - prev.y) * 1.15 : 0;

  // camera: follows the rider, then pushes in on the pin
  const push = interpolate(frame, [30 * k, durationInFrames], [p.minZoom, p.zoom], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.out(Easing.cubic) });
  const focus = prev ? rider : cur;
  const fx = interpolate(push, [p.minZoom, p.zoom], [W / 2, focus.x]);
  const fy = interpolate(push, [p.minZoom, p.zoom], [H / 2, focus.y]);
  // keep the map edge out of frame once it is larger than the frame (no dark strip when the pin sits at the coast)
  const keep = (t: number, o: number, size: number, frameSize: number) =>
    size * push >= frameSize ? Math.min(-o * push, Math.max(frameSize - (o + size) * push, t)) : t;
  const tx = keep(W / 2 - fx * push, ox, mw, W);
  const ty0 = keep(H / 2 - fy * push, oy, mh, H);
  const ty = p.minZoom > 1 && mh * push < H ? Math.min(ty0, -oy * push) : ty0;

  const pop = spring({ frame: frame - (prev ? 46 : 8) * k, fps, config: { damping: 9, stiffness: 160 } });
  const card = spring({ frame: frame - 4 * k, fps, config: { damping: 14, stiffness: 180 } });
  const pulse = 1 + 0.18 * Math.sin(frame / (6 * k));

  return (
    <AbsoluteFill style={{ background: "radial-gradient(ellipse at 50% 45%, #1d4f86 0%, #0b2547 70%, #071a33 100%)" }}>
      <style dangerouslySetInnerHTML={{ __html: fontFaceCSS }} />
      <div style={{ position: "absolute", left: 0, top: 0, width: W, height: H, transform: `translate(${tx}px, ${ty}px) scale(${push})`, transformOrigin: "0 0" }}>
        <Img src={staticFile(p.map)} style={{ position: "absolute", left: ox, top: oy, width: mw, height: mh }} />
        <svg width={W} height={H} style={{ position: "absolute", left: 0, top: 0, overflow: "visible" }}>
          {/* earlier finds stay as small ticked dots */}
          {p.pins.slice(0, p.active).map((_, i) => {
            const c = P(i);
            return <g key={i}><circle cx={c.x} cy={c.y} r={11 / push} fill={p.route} stroke="#000" strokeWidth={3 / push} /></g>;
          })}
          {prev && <path d={path} fill="none" stroke={p.route} strokeWidth={9 / push} strokeLinecap="round"
            strokeDasharray={len} strokeDashoffset={len * (1 - draw)} style={{ filter: `drop-shadow(0 0 ${8 / push}px ${p.route})` }} />}
          {prev && draw < 1 && <circle cx={rider.x} cy={rider.y} r={20 / push} fill="#111" stroke={p.route} strokeWidth={5 / push} />}
          {/* the new pin */}
          <g transform={`translate(${cur.x} ${cur.y}) scale(${Math.max(0, pop) / push})`}>
            <circle r={62 * pulse} fill={p.accent} opacity={0.25} />
            <circle r={42} fill={p.accent} stroke="#000" strokeWidth={5} />
            <text y={16} textAnchor="middle" fontSize={46} fontFamily="Bangers" fill="#fff" stroke="#000" strokeWidth={1.5}>{p.active + 1}</text>
          </g>
        </svg>
      </div>
      {/* counter card, like the reference's timer box */}
      <div style={{ position: "absolute", top: 250, left: 0, width: W, display: "flex", justifyContent: "center",
        transform: `scale(${0.6 + 0.4 * card})`, opacity: card }}>
        <div style={{ background: "rgba(8,8,10,0.92)", border: `4px solid ${p.accent}`, borderRadius: 22, padding: "16px 34px",
          boxShadow: `0 0 26px ${p.accent}88`, textAlign: "center" }}>
          <div style={{ fontFamily: "Bangers", fontSize: 92, color: "#fff", letterSpacing: 2, lineHeight: 1 }}>
            <span style={{ color: p.route }}>#{p.active + 1}</span> {p.label}
          </div>
          {p.region && <div style={{ fontFamily: "Montserrat", fontWeight: 800, fontSize: 36, color: "#cfd8e3", marginTop: 6 }}>{p.region}</div>}
        </div>
      </div>
      {/* neon frame */}
      <AbsoluteFill style={{ border: `10px solid ${p.accent}`, boxShadow: `inset 0 0 40px ${p.accent}66`, opacity: 0.9 * card }} />
    </AbsoluteFill>
  );
};
