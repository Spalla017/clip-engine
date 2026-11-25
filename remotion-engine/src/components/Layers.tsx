import React from "react";
import {
  AbsoluteFill,
  Easing,
  Html5Audio,
  Img,
  Loop,
  OffthreadVideo,
  Sequence,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import type { Broll, Card, Crop, CropKeyframe, ListCard, Main, Sfx, ZoomTarget } from "../types";

/** Where the source video sits inside a box, so the magnifier can reuse the same maths. */
export interface Placement {
  scale: number;
  left: number;
  top: number;
}

const cropXAt = (crop: Crop, keyframes: CropKeyframe[] | undefined, t: number) => {
  if (!keyframes || keyframes.length < 2) return crop.x;
  return interpolate(t, keyframes.map((k) => k.t), keyframes.map((k) => k.x), {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
};

/** Cover a box with the crop region (or with the whole video when there is no crop). */
export const placeMain = (main: Main, boxW: number, boxH: number, t: number): Placement => {
  const crop = main.crop ?? { x: 0, y: 0, w: main.sourceWidth, h: main.sourceHeight };
  const x = cropXAt(crop, main.cropKeyframes, t);
  const scale = Math.max(boxW / crop.w, boxH / crop.h);
  return {
    scale,
    left: -x * scale + (boxW - crop.w * scale) / 2,
    top: -crop.y * scale + (boxH - crop.h * scale) / 2,
  };
};

/** Main clip (reframed). Punch-in on the first frames, as in the half-and-half style. */
export const MainVideo: React.FC<{ main: Main; boxW: number; boxH: number }> = ({ main, boxW, boxH }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const p = placeMain(main, boxW, boxH, frame / fps);
  const punch = interpolate(frame, [0, Math.round(fps * 0.3)], [1.1, 1], {
    extrapolateRight: "clamp",
    easing: Easing.out(Easing.cubic),
  });
  const video = (
    <OffthreadVideo
      src={main.src}
      volume={main.volume}
      muted={main.volume === 0}
      style={{
        position: "absolute",
        width: main.sourceWidth * p.scale,
        height: main.sourceHeight * p.scale,
        left: p.left,
        top: p.top,
      }}
    />
  );
  const loopFrames = Math.max(1, Math.floor((main.durationInSeconds ?? 0) * fps) - 2);
  return (
    <div style={{ position: "absolute", inset: 0, overflow: "hidden", background: "#000" }}>
      <AbsoluteFill style={{ transform: `scale(${punch})` }}>
        {main.loop && main.durationInSeconds ? <Loop durationInFrames={loopFrames}>{video}</Loop> : video}
      </AbsoluteFill>
    </div>
  );
};

const BrollClip: React.FC<{ clip: Broll; index: number; frames: number }> = ({ clip, index, frames }) => {
  const frame = useCurrentFrame();
  // Soft Ken Burns: alternate push-in / pull-out so consecutive cuts never feel the same.
  const [a, b] = index % 2 === 0 ? [1.02, 1.1] : [1.1, 1.02];
  const s = interpolate(frame, [0, frames], [a, b], { extrapolateRight: "clamp" });
  return (
    <AbsoluteFill style={{ transform: `scale(${s}) scaleX(${clip.flip ? -1 : 1})` }}>
      <AbsoluteFill style={{ transform: `scale(${clip.zoom ?? 1})`, transformOrigin: clip.origin === "center" ? "50% 50%" : "50% 0%" }}>
        <OffthreadVideo src={clip.src} muted style={{ width: "100%", height: "100%", objectFit: "cover" }} />
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

/** Silent gameplay cut every few seconds. Repeats the list if the video is longer than the footage. */
export const BrollTrack: React.FC<{ broll: Broll[]; totalFrames: number }> = ({ broll, totalFrames }) => {
  const { fps } = useVideoConfig();
  if (!broll.length) return <AbsoluteFill style={{ background: "#0b0b12" }} />;
  const items: { clip: Broll; from: number; frames: number; index: number }[] = [];
  let offset = 0;
  let i = 0;
  while (offset < totalFrames && i < 400) {
    const clip = broll[i % broll.length];
    const frames = Math.max(2, Math.round(clip.dur * fps) - 1);
    items.push({ clip, from: offset, frames, index: i });
    offset += frames;
    i++;
  }
  return (
    <div style={{ position: "absolute", inset: 0, overflow: "hidden", background: "#000" }}>
      {items.map((it) => (
        <Sequence key={it.index} from={it.from} durationInFrames={it.frames}>
          <BrollClip clip={it.clip} index={it.index} frames={it.frames} />
        </Sequence>
      ))}
    </div>
  );
};

/** Fixed headline pill inside the safe zone. Enters after the hook leaves. */
export const Headline: React.FC<{ text: string; accent: string; top: number; from?: number }> = ({
  text,
  accent,
  top,
  from = 3.5,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const local = frame - Math.round(from * fps);
  if (!text || local < 0) return null;
  const s = spring({ frame: local, fps, config: { mass: 1, damping: 15, stiffness: 180 } });
  return (
    <div style={{ position: "absolute", top, left: 60, right: 60, display: "flex", justifyContent: "center" }}>
      <div
        style={{
          transform: `scale(${s})`,
          fontFamily: "'Montserrat', sans-serif",
          fontWeight: 800,
          // a headline fixed from frame 0 is the video's title bar (viral TikTok refs, 2026-11-18): bigger
          fontSize: from === 0 ? 54 : 40,
          lineHeight: 1.15,
          textAlign: "center",
          textTransform: "uppercase",
          color: "#fff",
          padding: "14px 26px",
          borderRadius: 18,
          background: "rgba(8,6,20,0.78)",
          border: `4px solid ${accent}`,
          boxShadow: `0 0 26px ${accent}88`,
        }}
      >
        {text}
      </div>
    </div>
  );
};

/** Floating print (Reddit post, news headline). Pops in, fades out. */
export const OverlayCard: React.FC<{ card: Card; top: number; height: number }> = ({ card, top, height }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const frames = Math.max(12, Math.round(card.dur * fps));
  const inn = interpolate(frame, [0, fps * 0.27], [0, 1], {
    extrapolateRight: "clamp",
    easing: Easing.out(Easing.back(1.6)),
  });
  const out = interpolate(frame, [frames - fps * 0.2, frames], [1, 0], { extrapolateLeft: "clamp" });
  return (
    <div
      style={{
        position: "absolute",
        left: 0,
        width: 1080,
        top,
        height,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        opacity: Math.min(inn, out),
        transform: `scale(${0.9 + 0.1 * inn}) rotate(${-1.5 + 1.5 * inn}deg)`,
      }}
    >
      <Img
        src={card.src}
        style={{
          maxWidth: 860,
          maxHeight: height,
          objectFit: "contain",
          borderRadius: 26,
          border: "5px solid #fff",
          boxShadow: "0 18px 48px rgba(0,0,0,0.55)",
        }}
      />
    </div>
  );
};

export const Cards: React.FC<{ cards: Card[]; top: number; height: number }> = ({ cards, top, height }) => {
  const { fps } = useVideoConfig();
  return (
    <>
      {cards.map((c, i) => (
        <Sequence key={i} from={Math.round(c.at * fps)} durationInFrames={Math.max(12, Math.round(c.dur * fps))}>
          <OverlayCard card={c} top={top} height={height} />
        </Sequence>
      ))}
    </>
  );
};

/** Magnifier with a pulsing neon ring over a detail of the main clip. */
export const EasterEggZoom: React.FC<{
  target: ZoomTarget;
  main: Main;
  boxW: number;
  boxH: number;
  accent: string;
}> = ({ target, main, boxW, boxH, accent }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const startFrame = Math.round(target.at * fps);
  const frames = Math.round(target.dur * fps);
  const p = placeMain(main, boxW, boxH, (startFrame + frame) / fps);
  const r = target.r;
  // Lens sits over the detail, kept inside the box.
  const px = Math.min(boxW - r - 20, Math.max(r + 20, p.left + target.x * main.sourceWidth * p.scale));
  const py = Math.min(boxH - r - 20, Math.max(r + 20, p.top + target.y * main.sourceHeight * p.scale));
  const k = p.scale * target.scale;
  const pop = spring({ frame, fps, config: { mass: 1, damping: 13, stiffness: 170 } });
  const out = interpolate(frame, [frames - fps * 0.2, frames], [1, 0], { extrapolateLeft: "clamp" });
  const pulse = 0.5 + 0.5 * Math.sin((frame / fps) * Math.PI * 3);
  return (
    <div
      style={{
        position: "absolute",
        left: px - r,
        top: py - r,
        width: r * 2,
        height: r * 2,
        borderRadius: "50%",
        overflow: "hidden",
        opacity: out,
        transform: `scale(${pop})`,
        border: `8px solid ${accent}`,
        boxShadow: `0 0 ${24 + 30 * pulse}px ${accent}, inset 0 0 22px rgba(0,0,0,0.55)`,
        background: "#000",
      }}
    >
      <OffthreadVideo
        src={main.src}
        muted
        trimBefore={main.loop ? 0 : startFrame}
        style={{
          position: "absolute",
          width: main.sourceWidth * k,
          height: main.sourceHeight * k,
          left: r - target.x * main.sourceWidth * k,
          top: r - target.y * main.sourceHeight * k,
        }}
      />
    </div>
  );
};

export const Zooms: React.FC<{ targets: ZoomTarget[]; main?: Main; boxW: number; boxH: number; accent: string }> = ({
  targets,
  main,
  boxW,
  boxH,
  accent,
}) => {
  const { fps } = useVideoConfig();
  if (!main) return null;
  return (
    <>
      {targets.map((t, i) => (
        <Sequence key={i} from={Math.round(t.at * fps)} durationInFrames={Math.max(6, Math.round(t.dur * fps))}>
          <EasterEggZoom target={t} main={main} boxW={boxW} boxH={boxH} accent={accent} />
        </Sequence>
      ))}
    </>
  );
};

/**
 * Magnifier over gameplay cuts (no main clip). x,y are 0..1 of the box on screen.
 * Not wrapped in a Sequence: the inner BrollTrack must stay on the global timeline.
 */
export const BrollZooms: React.FC<{
  targets: ZoomTarget[];
  broll: Broll[];
  totalFrames: number;
  boxW: number;
  boxH: number;
  accent: string;
}> = ({ targets, broll, totalFrames, boxW, boxH, accent }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  return (
    <>
      {targets.map((t, i) => {
        const frames = Math.max(6, Math.round(t.dur * fps));
        const local = frame - Math.round(t.at * fps);
        if (local < 0 || local >= frames) return null;
        const r = t.r;
        const cx = Math.min(boxW - r - 20, Math.max(r + 20, t.x * boxW));
        const cy = Math.min(boxH - r - 20, Math.max(r + 20, t.y * boxH));
        const pop = spring({ frame: local, fps, config: { mass: 1, damping: 13, stiffness: 170 } });
        const out = interpolate(local, [frames - fps * 0.2, frames], [1, 0], { extrapolateLeft: "clamp" });
        const pulse = 0.5 + 0.5 * Math.sin((local / fps) * Math.PI * 3);
        return (
          <div
            key={i}
            style={{
              position: "absolute",
              left: cx - r,
              top: cy - r,
              width: r * 2,
              height: r * 2,
              borderRadius: "50%",
              overflow: "hidden",
              opacity: out,
              transform: `scale(${pop})`,
              border: `8px solid ${accent}`,
              boxShadow: `0 0 ${24 + 30 * pulse}px ${accent}, inset 0 0 22px rgba(0,0,0,0.55)`,
              background: "#000",
            }}
          >
            <div
              style={{
                position: "absolute",
                left: r - t.x * boxW,
                top: r - t.y * boxH,
                width: boxW,
                height: boxH,
                transform: `scale(${t.scale})`,
                transformOrigin: `${t.x * boxW}px ${t.y * boxH}px`,
              }}
            >
              <BrollTrack broll={broll} totalFrames={totalFrames} />
            </div>
          </div>
        );
      })}
    </>
  );
};

const ListCardPill: React.FC<{ card: ListCard; accent: string; top: number }> = ({ card, accent, top }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const frames = Math.max(12, Math.round(card.dur * fps));
  const pop = spring({ frame, fps, config: { mass: 1, damping: 14, stiffness: 190 } });
  const out = interpolate(frame, [frames - fps * 0.2, frames], [1, 0], { extrapolateLeft: "clamp" });
  const size = card.label.length > 16 ? 46 : 56;
  return (
    <div style={{ position: "absolute", top, left: 50, right: 50, display: "flex", justifyContent: "center", opacity: out }}>
      <div
        style={{
          transform: `scale(${pop})`,
          display: "flex",
          alignItems: "center",
          gap: 18,
          padding: "16px 30px",
          borderRadius: 22,
          background: "rgba(8,6,20,0.86)",
          borderLeft: `10px solid ${accent}`,
          boxShadow: "0 12px 34px rgba(0,0,0,0.5)",
          fontFamily: "'Montserrat', sans-serif",
          fontWeight: 800,
          fontSize: size,
          lineHeight: 1.1,
          textTransform: "uppercase",
          whiteSpace: "nowrap",
        }}
      >
        <span style={{ color: accent }}>{card.index}</span>
        <span style={{ color: "rgba(255,255,255,0.55)" }}>•</span>
        <span style={{ color: "#fff" }}>{card.label}</span>
      </div>
    </div>
  );
};

/** Numbered labels that follow an enumeration in the narration ("Number two: the Leonida Keys"). */
export const ListCards: React.FC<{ cards: ListCard[]; accent: string; top: number }> = ({ cards, accent, top }) => {
  const { fps } = useVideoConfig();
  return (
    <>
      {cards.map((c, i) => (
        <Sequence key={i} from={Math.round(c.at * fps)} durationInFrames={Math.max(12, Math.round(c.dur * fps))}>
          <ListCardPill card={c} accent={accent} top={top} />
        </Sequence>
      ))}
    </>
  );
};

/**
 * Music ducking (2026-11-19, earshot on our GTA 6 Short: 10 drops of 0.3-0.8 s between voice takes, music inaudible;
 * the clip tutorials keep the bed audible): under speech the bed sits at `base`, and in any pause >= 0.28 s (and before
 * the first / after the last word) it rises to 2.2x base with 0.07 s ramps (our voice takes leave 0.3-0.5 s gaps), so the track never goes quiet.
 */
const duckCurve = (speech: { startMs: number; endMs: number }[], base: number) => {
  const spans: [number, number][] = [];
  for (const c of [...speech].sort((a, b) => a.startMs - b.startMs)) {
    const s = c.startMs / 1000 - 0.04, e = c.endMs / 1000 + 0.04;
    const last = spans[spans.length - 1];
    if (last && s - last[1] < 0.2) last[1] = Math.max(last[1], e);
    else spans.push([s, e]);
  }
  return (t: number) => {
    let d = 1e9;  // distance (s) into the nearest speech span; 0 = outside
    let inside = false;
    for (const [s, e] of spans) {
      if (t >= s && t <= e) { inside = true; d = Math.min(t - s, e - t); break; }
    }
    const k = inside ? Math.min(1, d / 0.07) : 0;  // 1 = fully ducked
    return base * (2.2 - 1.2 * k);
  };
};

/** Narration plus sound effects. */
export const AudioLayer: React.FC<{ narrationSrc: string; sfx: Sfx[]; musicSrc?: string; musicVolume?: number;
  speech?: { startMs: number; endMs: number }[] }> = ({
  narrationSrc,
  sfx,
  musicSrc,
  musicVolume,
  speech,
}) => {
  const { fps } = useVideoConfig();
  const base = musicVolume ?? 0.08;
  const curve = narrationSrc && speech && speech.length ? duckCurve(speech, base) : null;
  return (
    <>
      {musicSrc ? <Html5Audio src={musicSrc} volume={curve ? (f: number) => curve(f / fps) : base} loop /> : null}
      {narrationSrc ? <Html5Audio src={narrationSrc} /> : null}
      {sfx.map((s, i) => (
        <Sequence key={i} from={Math.max(0, Math.round(s.at * fps))} durationInFrames={Math.round(3 * fps)}>
          <Html5Audio src={s.src} volume={s.volume} />
        </Sequence>
      ))}
    </>
  );
};

/** "POV: ..." style text: big, white, black outline, stays the whole video. */
export const PovText: React.FC<{ text: string; top: number }> = ({ text, top }) =>
  text ? (
    <div
      style={{
        position: "absolute",
        top,
        left: 70,
        right: 70,
        textAlign: "center",
        fontFamily: "'Montserrat', sans-serif",
        fontWeight: 800,
        fontSize: text.length > 60 ? 50 : 62,
        lineHeight: 1.15,
        color: "#fff",
        WebkitTextStroke: "10px #000",
        paintOrder: "stroke fill",
        textShadow: "0 6px 0 rgba(0,0,0,0.35)",
      }}
    >
      {text}
    </div>
  ) : null;

/** Label chip of one half in the comparison layout ("GTA 5" / "GTA 6"). */
export const CompareLabel: React.FC<{ text: string; top: number; accent: string }> = ({ text, top, accent }) =>
  text ? (
    <div
      style={{
        position: "absolute",
        top,
        left: 40,
        padding: "12px 26px",
        borderRadius: 16,
        background: "rgba(8,6,20,0.88)",
        border: `4px solid ${accent}`,
        fontFamily: "'Montserrat', sans-serif",
        fontWeight: 800,
        fontSize: 46,
        color: "#fff",
        textTransform: "uppercase",
      }}
    >
      {text}
    </div>
  ) : null;

/** Small source credit inside the safe zone: bottom-left, or top-left (`top`) when the captions own the bottom band. */
export const Credit: React.FC<{ text: string; bottom?: number; top?: number }> = ({ text, bottom, top }) =>
  text ? (
    <div
      style={{
        position: "absolute",
        left: 48,
        ...(top !== undefined ? { top } : { bottom: bottom ?? 380 }),
        fontFamily: "'Inter', sans-serif",
        fontWeight: 700,
        fontSize: 26,
        color: "rgba(255,255,255,0.92)",
        textShadow: "0 2px 6px rgba(0,0,0,0.9)",
      }}
    >
      {text}
    </div>
  ) : null;
