import { useCurrentFrame, useVideoConfig, spring } from "remotion";
import { useCaptionPages } from "../hooks/useCaptionPages";
import { BOLD_THEME } from "../styles/theme";
import type { Caption } from "../types";

interface BoldCaptionsProps {
  captions: Caption[];
}

/**
 * Bold-style captions: ALL CAPS, pop-in spring animation,
 * yellow highlight on the currently active word.
 *
 * Font: Montserrat Bold
 * Words per page: 2-3 (800ms combine window)
 * Animation: Pop-in spring {mass:1, damping:12, stiffness:200}
 */
export const BoldCaptions: React.FC<BoldCaptionsProps> = ({
  captions,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const currentTimeMs = (frame / fps) * 1000;

  const pages = useCaptionPages(captions, 800);

  // Find the current page
  const currentPage = pages.find(
    (p) =>
      currentTimeMs >= p.startMs &&
      currentTimeMs < p.startMs + p.durationMs
  );

  if (!currentPage) return null;

  // Long pages shrink so one line never passes ~880 px, even at the top of the pop-in overshoot.
  const chars = currentPage.tokens.reduce((n, t) => n + t.text.trim().length + 1, 0);
  const fontSize = Math.round(Math.min(72, (72 * 16) / Math.max(1, chars)) < 54 ? 54 : Math.min(72, (72 * 16) / Math.max(1, chars)));

  const pageStartFrame = Math.floor((currentPage.startMs / 1000) * fps);
  const localFrame = frame - pageStartFrame;

  // Pop-in spring animation
  const scale = spring({
    frame: localFrame,
    fps,
    config: { mass: 1, damping: 12, stiffness: 200 },
  });

  return (
    <div
      style={{
        position: "absolute",
        bottom: 350,
        left: 90,
        right: 90,
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
        flexWrap: "wrap",
        gap: 12,
        transform: `scale(${scale})`,
      }}
    >
      {currentPage.tokens.map((token, i) => {
        const isActive =
          currentTimeMs >= token.fromMs && currentTimeMs < token.toMs;

        return (
          <span
            key={`${currentPage.startMs}-${i}`}
            style={{
              fontFamily: "'Montserrat', sans-serif",
              fontWeight: 800,
              fontSize,
              textTransform: "uppercase",
              color: isActive
                ? BOLD_THEME.activeColor
                : BOLD_THEME.textColor,
              textShadow: `
                3px 3px 0 ${BOLD_THEME.shadowColor},
                -3px -3px 0 ${BOLD_THEME.shadowColor},
                3px -3px 0 ${BOLD_THEME.shadowColor},
                -3px 3px 0 ${BOLD_THEME.shadowColor}
              `,
              lineHeight: 1.1,
              textAlign: "center",
            }}
          >
            {token.text.trim().toUpperCase()}
          </span>
        );
      })}
    </div>
  );
};
