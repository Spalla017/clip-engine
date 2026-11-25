import React from "react";
import { AbsoluteFill, useVideoConfig } from "remotion";
import { Captions } from "./components/Captions";
import { HookOverlay } from "./components/HookOverlay";
import { ProgressBar } from "./components/ProgressBar";
import { AudioLayer, BrollTrack, BrollZooms, Cards, Credit, Headline, ListCards, PovText, MainVideo, Zooms } from "./components/Layers";
import { InfoCards } from "./components/InfoCards";
import { ListBadge, UiCards } from "./components/UiCards";
import { Arrows, Stickers, TopBadge } from "./components/ModelOverlays";
import { fontFaceCSS } from "./styles/fonts";
import { H, W, type ShortProps } from "./types";

// Safe zone shared by TikTok, Reels and Shorts: keep text out of the top 200 px and the bottom 350 px.
const SAFE_TOP = 200;
// "card" layout: a 16:9 window under the headline, above the caption band (captions sit at bottom 350).
const CARD_W = 1000;
const CARD_H = 562;
const CARD_TOP = 470;

/** Fullscreen 9:16: main clip (reframed) or gameplay cuts, hook, headline, cards, captions. */
export const ClassicViralShort: React.FC<ShortProps> = (p) => {
  const { fps } = useVideoConfig();
  const total = Math.ceil(p.durationInSeconds * fps);
  const card = p.layout === "card";
  return (
    <AbsoluteFill style={{ backgroundColor: "black" }}>
      <style dangerouslySetInnerHTML={{ __html: fontFaceCSS }} />
      {card ? (
        <>
          {/* blurred, darkened copy fills the 9:16 frame; the sharp 16:9 shot sits in a rounded card */}
          <AbsoluteFill style={{ filter: "blur(38px) brightness(0.55) saturate(1.2)", transform: "scale(1.15)" }}>
            {p.main ? <MainVideo main={p.main} boxW={W} boxH={H} /> : <BrollTrack broll={p.broll} totalFrames={total} />}
          </AbsoluteFill>
          <div style={{ position: "absolute", left: (W - CARD_W) / 2, top: CARD_TOP, width: CARD_W, height: CARD_H,
            borderRadius: 34, overflow: "hidden", border: `5px solid ${p.accent}`,
            boxShadow: `0 0 40px ${p.accent}66, 0 30px 60px rgba(0,0,0,0.6)` }}>
            {p.main ? <MainVideo main={p.main} boxW={CARD_W} boxH={CARD_H} /> : <BrollTrack broll={p.broll} totalFrames={total} />}
          </div>
        </>
      ) : (
        <>
          {p.main ? <MainVideo main={p.main} boxW={W} boxH={H} /> : <BrollTrack broll={p.broll} totalFrames={total} />}
          {p.main ? (
            <Zooms targets={p.zoomTargets} main={p.main} boxW={W} boxH={H} accent={p.accent} />
          ) : (
            <BrollZooms targets={p.zoomTargets} broll={p.broll} totalFrames={total} boxW={W} boxH={H} accent={p.accent} />
          )}
        </>
      )}
      <Cards cards={p.cards} top={520} height={640} />
      <InfoCards cards={p.infoCards} accent={p.accent} top={500} />
      {/* Darken the caption band a little so white text reads over bright gameplay. */}
      {p.captionStyle !== "word" && <div
        style={{
          position: "absolute",
          left: 0,
          width: W,
          bottom: 250,
          height: 420,
          background: "linear-gradient(180deg, rgba(0,0,0,0), rgba(0,0,0,0.45) 50%, rgba(0,0,0,0))",
        }}
      />}
      <Captions captions={p.captions} style={p.captionStyle} emphasis={p.emphasis} />
      <div style={{ position: "absolute", top: SAFE_TOP - 40, left: 0, width: W, height: 400 }}>
        {(p.hookLine1 || p.hookLine2) && <HookOverlay line1={p.hookLine1} line2={p.hookLine2} />}
      </div>
      <Headline text={p.headline} accent={p.accent} top={SAFE_TOP} from={p.headlineFrom} />
      <ListCards cards={p.listCards} accent={p.accent} top={card ? CARD_TOP + CARD_H + 30 : SAFE_TOP + 96} />
      {p.listBadge && <ListBadge cards={p.listCards} accent={p.accent} top={card ? CARD_TOP - 64 : SAFE_TOP + 110} left={card ? 30 : 60} />}
      <UiCards cards={p.uiCards} accent={p.accent} top={card ? CARD_TOP + 60 : 560} />
      <Credit text={p.credit} top={395} />  {/* captions sit at ~1520 px: a bottom credit collided with them (2026-11-18) */}
      <PovText text={p.povText} top={p.headline ? SAFE_TOP + 110 : SAFE_TOP + 20} />
      <Stickers items={p.stickers} />
      <Arrows items={p.arrows} />
      <TopBadge badge={p.badge} total={total} />
      <AudioLayer narrationSrc={p.narrationSrc} sfx={p.sfx} musicSrc={p.musicSrc} musicVolume={p.musicVolume} speech={p.captions} />
      {p.showProgressBar && <ProgressBar durationInSeconds={p.durationInSeconds} />}
    </AbsoluteFill>
  );
};
