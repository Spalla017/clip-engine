import React from "react";
import { AbsoluteFill, useVideoConfig } from "remotion";
import { Captions } from "./components/Captions";
import { HookOverlay } from "./components/HookOverlay";
import { ProgressBar } from "./components/ProgressBar";
import { AudioLayer, BrollTrack, Cards, CompareLabel, Credit, Headline, ListCards, MainVideo, PovText, Zooms } from "./components/Layers";
import { InfoCards } from "./components/InfoCards";
import { Arrows, Stickers, TopBadge } from "./components/ModelOverlays";
import { fontFaceCSS } from "./styles/fonts";
import { H, HALF, W, type ShortProps } from "./types";

// The caption components sit 350 px above the bottom of their container.
// A container this tall puts the caption line right on the divider (y = 960).
const CAPTION_BOX = HALF + 350 + 50;

/** Half-and-half: main clip on top, hypnotic gameplay below, caption on the divider, optional magnifier. */
export const SplitAndZoomShort: React.FC<ShortProps> = (p) => {
  const { fps } = useVideoConfig();
  const total = Math.ceil(p.durationInSeconds * fps);
  // Without a main clip the top half takes every other gameplay piece.
  const top = p.broll.filter((_, i) => i % 2 === 0);
  const bottom = p.main ? p.broll : p.broll.filter((_, i) => i % 2 === 1);
  return (
    <AbsoluteFill style={{ backgroundColor: "black" }}>
      <style dangerouslySetInnerHTML={{ __html: fontFaceCSS }} />
      <div style={{ position: "absolute", left: 0, top: 0, width: W, height: HALF, overflow: "hidden" }}>
        {p.main ? <MainVideo main={p.main} boxW={W} boxH={HALF} /> : <BrollTrack broll={top} totalFrames={total} />}
        <Zooms targets={p.zoomTargets} main={p.main} boxW={W} boxH={HALF} accent={p.accent} />
      </div>
      <div style={{ position: "absolute", left: 0, top: HALF, width: W, height: H - HALF, overflow: "hidden" }}>
        {p.main2 ? (
          <MainVideo main={p.main2} boxW={W} boxH={H - HALF} />
        ) : (
          <BrollTrack broll={bottom.length ? bottom : p.broll} totalFrames={total} />
        )}
      </div>
      <CompareLabel text={p.compareLabels[0] ?? ""} top={300} accent={p.accent} />
      <CompareLabel text={p.compareLabels[1] ?? ""} top={HALF + 110} accent={p.accent} />
      <Cards cards={p.cards} top={HALF + 110} height={H - HALF - 480} />
      <InfoCards cards={p.infoCards} accent={p.accent} top={500} />
      <div
        style={{
          position: "absolute",
          left: 0,
          width: W,
          top: HALF - 170,
          height: 340,
          background: "linear-gradient(180deg, rgba(0,0,0,0), rgba(0,0,0,0.62) 50%, rgba(0,0,0,0))",
        }}
      />
      <div style={{ position: "absolute", left: 0, top: 0, width: W, height: CAPTION_BOX }}>
        <Captions captions={p.captions} style={p.captionStyle} emphasis={p.emphasis} />
      </div>
      <div style={{ position: "absolute", top: 160, left: 0, width: W, height: 400 }}>
        {(p.hookLine1 || p.hookLine2) && <HookOverlay line1={p.hookLine1} line2={p.hookLine2} />}
      </div>
      <Headline text={p.headline} accent={p.accent} top={200} />
      <ListCards cards={p.listCards} accent={p.accent} top={296} />
      <Credit text={p.credit} bottom={380} />  {/* above the 350 px app-button zone */}
      <PovText text={p.povText} top={p.headline ? 310 : 220} />
      <Stickers items={p.stickers} />
      <Arrows items={p.arrows} />
      <TopBadge badge={p.badge} total={total} />
      <AudioLayer narrationSrc={p.narrationSrc} sfx={p.sfx} musicSrc={p.musicSrc} musicVolume={p.musicVolume} speech={p.captions} />
      {p.showProgressBar && <ProgressBar durationInSeconds={p.durationInSeconds} />}
    </AbsoluteFill>
  );
};
