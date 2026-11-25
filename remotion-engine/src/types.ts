import { z } from "zod";

export const CaptionSchema = z.object({
  text: z.string(),
  startMs: z.number(),
  endMs: z.number(),
});
export type Caption = z.infer<typeof CaptionSchema>;

export const CropSchema = z.object({ x: z.number(), y: z.number(), w: z.number(), h: z.number() });
export type Crop = z.infer<typeof CropSchema>;

export const CropKeyframeSchema = z.object({ t: z.number(), x: z.number() });
export type CropKeyframe = z.infer<typeof CropKeyframeSchema>;

export const CaptionStyle = z.enum(["bold", "bounce", "clean", "word"]);
export type CaptionStyleType = z.infer<typeof CaptionStyle>;

// Main clip (streamer, podcast, translated source). Already trimmed by the pipeline, starts at 0.
export const MainSchema = z.object({
  src: z.string(),
  sourceWidth: z.number(),
  sourceHeight: z.number(),
  crop: CropSchema.optional(),
  cropKeyframes: z.array(CropKeyframeSchema).optional().default([]),
  // 0 = original audio removed (trailers, translated sources). 1 = speech kept (cuts).
  volume: z.number().optional().default(1),
  loop: z.boolean().optional().default(false),
  durationInSeconds: z.number().optional(),
});
export type Main = z.infer<typeof MainSchema>;

// Gameplay / B-roll piece. Already trimmed and silent (-an).
export const BrollSchema = z.object({
  src: z.string(),
  dur: z.number(),
  flip: z.boolean().optional().default(false),
  // > 1 enlarges from the top edge, pushing a burned-in bar at the foot of the shot out of frame.
  zoom: z.number().optional().default(1),
  // "center" removes letterbox bars (top and bottom) instead.
  origin: z.enum(["top", "center"]).optional().default("top"),
});
export type Broll = z.infer<typeof BrollSchema>;

export const CardSchema = z.object({ src: z.string(), at: z.number(), dur: z.number() });
export type Card = z.infer<typeof CardSchema>;

// Magnifier: x,y are 0..1 inside the source video; r is the lens radius in output pixels.
export const ZoomTargetSchema = z.object({
  at: z.number(),
  dur: z.number(),
  x: z.number(),
  y: z.number(),
  r: z.number().optional().default(210),
  scale: z.number().optional().default(2.4),
});
export type ZoomTarget = z.infer<typeof ZoomTargetSchema>;

// Compact numbered label for enumerations in the script: "02 • LEONIDA KEYS".
export const ListCardSchema = z.object({ at: z.number(), dur: z.number(), index: z.string(), label: z.string() });
export type ListCard = z.infer<typeof ListCardSchema>;
export const InfoCardSchema = z.object({ at: z.number(), dur: z.number(), src: z.string().optional(), kind: z.string().optional(),
  title: z.string().optional(), date: z.string().optional(), time: z.string().optional() });
export type InfoCard = z.infer<typeof InfoCardSchema>;

// Game-UI style card (study of viral GTA 6 TikToks, 2026-11-18): a dark rounded panel with a neon edge.
// bar = labelled meter that fills to `value` (0..1, left/right labels); counter = number running from `from` to `to`
// ("$25,000 → $0"); checklist = items ticking one by one.
export const UiCardSchema = z.object({
  at: z.number(), dur: z.number(), kind: z.enum(["bar", "counter", "checklist"]),
  title: z.string().optional().default(""),
  value: z.number().optional().default(0.8), left: z.string().optional().default(""), right: z.string().optional().default(""),
  from: z.number().optional().default(0), to: z.number().optional().default(0),
  prefix: z.string().optional().default(""), suffix: z.string().optional().default(""),
  items: z.array(z.string()).optional().default([]),
  // checklist: second (from the card start) each item is spoken; the item ticks then
  itemAt: z.array(z.number()).optional(),
  // own top (px in the 1080x1920 layout) and size, e.g. y 210 + scale 0.82 to keep a checklist off the action (2026-11-20)
  y: z.number().optional(), scale: z.number().optional(),
});
export type UiCard = z.infer<typeof UiCardSchema>;

// Model-Short overlays (references/estilo-modelos-2026-11-18.md). x, y = centre (sticker) or tip (arrow), 0..1 of the frame.
export const StickerSchema = z.object({
  at: z.number(), dur: z.number(), src: z.string().optional(), text: z.string().optional(),
  color: z.string().optional().default("#FFFFFF"),
  x: z.number().optional().default(0.5), y: z.number().optional().default(0.5),
  w: z.number().optional().default(420), // image width in px, or font size for a text sticker
  rot: z.number().optional().default(0),
  anim: z.enum(["pop", "none"]).optional().default("pop"),
  shadow: z.boolean().optional().default(true),
});
export type Sticker = z.infer<typeof StickerSchema>;
export const ArrowSchema = z.object({
  at: z.number(), dur: z.number(), x: z.number(), y: z.number(),
  angle: z.number().optional().default(90), len: z.number().optional().default(230),
  color: z.string().optional().default("#39FF14"),
});
export type Arrow = z.infer<typeof ArrowSchema>;
export const BadgeSchema = z.object({
  text: z.string(), from: z.number().optional().default(0), to: z.number().optional().default(0),
  top: z.number().optional().default(205),
});
export type Badge = z.infer<typeof BadgeSchema>;

export const SfxSchema =z.object({ src: z.string(), at: z.number(), volume: z.number().optional().default(0.5) });
export type Sfx = z.infer<typeof SfxSchema>;

export const ShortPropsSchema = z.object({
  fps: z.number().optional().default(60),
  durationInSeconds: z.number(),
  main: MainSchema.optional(),
  broll: z.array(BrollSchema).optional().default([]),
  narrationSrc: z.string().optional().default(""),
  // Background music (own / CC0 / licensed file), looped under everything else.
  musicSrc: z.string().optional().default(""),
  musicVolume: z.number().optional().default(0.08),
  // Second clip for the comparison layout (bottom half) and the label of each half.
  main2: MainSchema.optional(),
  compareLabels: z.array(z.string()).optional().default([]),
  // Big plain text that stays on screen ("POV: ..."), the native TikTok/Shorts text look.
  povText: z.string().optional().default(""),
  captions: z.array(CaptionSchema).optional().default([]),
  captionStyle: CaptionStyle.optional().default("bold"),
  hookLine1: z.string().optional().default(""),
  hookLine2: z.string().optional().default(""),
  headline: z.string().optional().default(""),
  accent: z.string().optional().default("#FF2E88"),
  cards: z.array(CardSchema).optional().default([]),
  listCards: z.array(ListCardSchema).optional().default([]),
  infoCards: z.array(InfoCardSchema).optional().default([]),
  uiCards: z.array(UiCardSchema).optional().default([]),
  // "card": gameplay inside a rounded 16:9 card over a blurred copy of itself (whole trailer shot visible, room
  // for a fixed headline above and captions below). "full": gameplay covers the 9:16 frame.
  layout: z.enum(["full", "card"]).optional().default("full"),
  // Second when the headline enters; 0 = fixed for the whole video.
  headlineFrom: z.number().optional().default(3.5),
  // Persistent round badge with the current list number (follows listCards).
  listBadge: z.boolean().optional().default(false),
  zoomTargets: z.array(ZoomTargetSchema).optional().default([]),
  // StreamerShort (stream clips, 2026-11-18): webcam and game rectangles in source pixels, and the scene list that
  // switches between them (reference: @gamescortestv style, cut every ~1.7 s).
  camBox: CropSchema.optional(),
  gameBox: CropSchema.optional(),
  scenes: z.array(z.object({ at: z.number(), dur: z.number(), layout: z.enum(["split", "splitFlip", "cam", "game"]),
    zoom: z.number().optional().default(1) })).optional().default([]),
  // Reaction hits on the stream's loudest moments (2026-11-18): fast punch-in, optional shake + white flash.
  punches: z.array(z.object({ at: z.number(), s: z.number().optional(), shake: z.boolean().optional() })).optional().default([]),
  // Paid-clipping layout (study of 5 clip tutorials, 2026-11-19): the headline sits in a yellow band on the cam/game seam.
  titleBand: z.boolean().optional(),
  // Model-Short overlays (2026-11-18): words to paint yellow in "word" captions, stickers, arrows, top badge.
  emphasis: z.array(z.string()).optional().default([]),
  stickers: z.array(StickerSchema).optional().default([]),
  arrows: z.array(ArrowSchema).optional().default([]),
  badge: BadgeSchema.optional(),
  sfx: z.array(SfxSchema).optional().default([]),
  credit: z.string().optional().default(""),
  showProgressBar: z.boolean().optional().default(true),
});
export type ShortProps = z.infer<typeof ShortPropsSchema>;

export const W = 1080;
export const H = 1920;
export const HALF = H / 2;
