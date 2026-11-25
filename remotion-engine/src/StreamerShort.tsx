import React from "react";
import { AbsoluteFill, Audio, OffthreadVideo, Sequence, interpolate, useCurrentFrame, useVideoConfig } from "remotion";
import { Captions } from "./components/Captions";
import { HookOverlay } from "./components/HookOverlay";
import { ProgressBar } from "./components/ProgressBar";
import { AudioLayer, Credit, Headline } from "./components/Layers";
import { Stickers } from "./components/ModelOverlays";
import { fontFaceCSS } from "./styles/fonts";
import { H, HALF, W, type Crop, type ShortProps } from "./types";

/**
 * Stream clip, edited (study of a viral GTA 6 streamer clip, 2026-11-18): the webcam and the game are cut out of
 * the same stream and shown as full cam, cam over game, game over cam, or game alone, switching every 1.5-3 s on the
 * speech. One continuous audio track (the stream) plays under all of it; the video copies are muted.
 */
type Scene = ShortProps["scenes"][number];

const Window: React.FC<{ src: string; srcW: number; srcH: number; box: Crop; w: number; h: number; from: number;
  zoom: number; frames: number }> = ({ src, srcW, srcH, box, w, h, from, zoom, frames }) => {
  const frame = useCurrentFrame();
  // cover-fit the crop into the window, then a slow punch-in for life
  const z = zoom * interpolate(frame, [0, frames], [1, 1.04], { extrapolateRight: "clamp" });
  const s = Math.max(w / box.w, h / box.h) * z;
  const left = -box.x * s + (w - box.w * s) / 2;
  const top = -box.y * s + (h - box.h * s) / 2;
  return (
    <div style={{ position: "absolute", inset: 0, overflow: "hidden", background: "#000" }}>
      <OffthreadVideo src={src} muted startFrom={from}
        style={{ position: "absolute", width: srcW * s, height: srcH * s, left, top, maxWidth: "none" }} />
    </div>
  );
};

const SceneView: React.FC<{ p: ShortProps; scene: Scene; frames: number }> = ({ p, scene, frames }) => {
  const { fps } = useVideoConfig();
  const m = p.main!;
  const full: Crop = { x: 0, y: 0, w: m.sourceWidth, h: m.sourceHeight };
  const cam = p.camBox ?? full;
  const game = p.gameBox ?? full;
  const from = Math.round(scene.at * fps);
  const win = (box: Crop, w: number, h: number, zoom = 1) => (
    <Window src={m.src} srcW={m.sourceWidth} srcH={m.sourceHeight} box={box} w={w} h={h} from={from} zoom={zoom} frames={frames} />
  );
  const half = (top: number, box: Crop, zoom = 1) => (
    <div style={{ position: "absolute", left: 0, top, width: W, height: HALF, overflow: "hidden" }}>{win(box, W, HALF, zoom)}</div>
  );
  if (scene.layout === "cam") return <AbsoluteFill>{win(cam, W, H, scene.zoom)}</AbsoluteFill>;
  if (scene.layout === "game") return <AbsoluteFill>{win(game, W, H, scene.zoom)}</AbsoluteFill>;
  const camTop = scene.layout === "split";
  return (
    <AbsoluteFill>
      {half(0, camTop ? cam : game, camTop ? scene.zoom : 1)}
      {half(HALF, camTop ? game : cam, camTop ? 1 : scene.zoom)}
      <div style={{ position: "absolute", left: 0, top: HALF - 3, width: W, height: 6, background: p.accent }} />
    </AbsoluteFill>
  );
};

// Reaction hit: 3-frame punch-in to 1+s, eased back over ~0.4 s; shake = decaying jitter + short white flash.
const hitFx = (frame: number, fps: number, punches: NonNullable<ShortProps["punches"]>) => {
  let scale = 1, dx = 0, dy = 0, flash = 0;
  const k = fps / 30;
  for (const h of punches) {
    const t = (frame - Math.round(h.at * fps)) / k;  // in 30 fps frames
    if (t < 0 || t > 16) continue;
    const s = h.s ?? 0.12;
    scale = Math.max(scale, 1 + s * (t < 3 ? t / 3 : Math.exp(-(t - 3) / 5)));
    if (h.shake && t < 10) {
      const a = 16 * (1 - t / 10);
      dx += Math.sin(t * 2.7 + h.at) * a;
      dy += Math.cos(t * 3.1 + h.at) * a * 0.7;
      flash = Math.max(flash, t < 4 ? 0.32 * (1 - t / 4) : 0);
    }
  }
  return { scale, dx, dy, flash };
};

// Yellow title band on the seam (paid-clipping reference, 2026-11-19): visible from frame 0, black heavy caps,
// one line (shrinks to fit), so the thumb frame already says what happens.
const TitleBand: React.FC<{ text: string }> = ({ text }) => {
  const size = Math.min(58, 1000 / Math.max(8, text.length * 0.62));
  return (
    <div style={{ position: "absolute", left: 0, width: W, top: HALF - 48, height: 96, display: "flex",
      alignItems: "center", justifyContent: "center", background: "#FFD400", boxShadow: "0 6px 18px rgba(0,0,0,0.45)" }}>
      <div style={{ fontFamily: "'Montserrat', sans-serif", fontWeight: 900, fontSize: size, color: "#0B0B0B",
        textTransform: "uppercase", whiteSpace: "nowrap", letterSpacing: 0.5 }}>{text}</div>
    </div>
  );
};

export const StreamerShort: React.FC<ShortProps> = (p) => {
  const { fps } = useVideoConfig();
  const frame = useCurrentFrame();
  if (!p.main) return <AbsoluteFill style={{ background: "#000" }} />;
  const scenes = p.scenes.length ? p.scenes : [{ at: 0, dur: p.durationInSeconds, layout: "split" as const, zoom: 1 }];
  const fx = hitFx(frame, fps, p.punches ?? []);
  const bandText = (p.headline || [p.hookLine1, p.hookLine2].filter(Boolean).join(" ")).trim();
  const band = !!(p.titleBand && bandText);
  return (
    <AbsoluteFill style={{ backgroundColor: "black" }}>
      <style dangerouslySetInnerHTML={{ __html: fontFaceCSS }} />
      <AbsoluteFill style={{ transform: `translate(${fx.dx}px, ${fx.dy}px) scale(${fx.scale})`, transformOrigin: "50% 40%" }}>
        {scenes.map((s, i) => {
          const frames = Math.max(1, Math.round(s.dur * fps));
          return (
            <Sequence key={i} from={Math.round(s.at * fps)} durationInFrames={frames}>
              <SceneView p={p} scene={s} frames={frames} />
            </Sequence>
          );
        })}
      </AbsoluteFill>
      {fx.flash > 0 && <AbsoluteFill style={{ background: "#fff", opacity: fx.flash }} />}
      {/* stream audio once, ducked by the pipeline's loudness pass */}
      <Audio src={p.main.src} volume={p.main.volume ?? 1} />
      {!band && <div style={{ position: "absolute", left: 0, width: W, top: HALF - 120, height: 360,
        background: "linear-gradient(180deg, rgba(0,0,0,0), rgba(0,0,0,0.5) 50%, rgba(0,0,0,0))" }} />}
      {/* caption line sits on the seam (y ~ 1010) like the 11-18 reference; with the title band it moves onto the game */}
      <div style={{ position: "absolute", left: 0, top: 0, width: W, height: band ? H : HALF + 350 + 50 }}>
        <Captions captions={p.captions} style={p.captionStyle} emphasis={p.emphasis ?? []} />
      </div>
      {band && <TitleBand text={bandText} />}
      <Stickers items={p.stickers ?? []} />
      <div style={{ position: "absolute", top: 160, left: 0, width: W, height: 400 }}>
        {!band && (p.hookLine1 || p.hookLine2) && <HookOverlay line1={p.hookLine1} line2={p.hookLine2} />}
      </div>
      {!band && <Headline text={p.headline} accent={p.accent} top={200} from={p.headlineFrom} />}
      <Credit text={p.credit} bottom={380} />
      <AudioLayer narrationSrc={p.narrationSrc} sfx={p.sfx} musicSrc={p.musicSrc} musicVolume={p.musicVolume} speech={p.captions} />
      {p.showProgressBar && <ProgressBar durationInSeconds={p.durationInSeconds} />}
    </AbsoluteFill>
  );
};
