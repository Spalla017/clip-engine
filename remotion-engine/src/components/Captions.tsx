import type { Caption, CaptionStyleType } from "../types";
import { BoldCaptions } from "./BoldCaptions";
import { BounceCaptions } from "./BounceCaptions";
import { CleanCaptions } from "./CleanCaptions";
import { WordCaptions } from "./ModelOverlays";

interface CaptionsProps {
  captions: Caption[];
  style: CaptionStyleType;
  emphasis?: string[];
}

/**
 * Style dispatcher — routes to the correct caption renderer.
 */
export const Captions: React.FC<CaptionsProps> = ({ captions, style, emphasis = [] }) => {
  if (!captions || captions.length === 0) return null;

  switch (style) {
    case "bold":
      return <BoldCaptions captions={captions} />;
    case "bounce":
      return <BounceCaptions captions={captions} />;
    case "word":
      return <WordCaptions captions={captions} emphasis={emphasis} />;
    case "clean":
      return <CleanCaptions captions={captions} />;
    default:
      return <BoldCaptions captions={captions} />;
  }
};
