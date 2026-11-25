import { useMemo } from "react";
import { createTikTokStyleCaptions } from "@remotion/captions";
import type { Caption } from "../types";

/**
 * Converts word-level captions into TikTok-style pages using
 * @remotion/captions createTikTokStyleCaptions().
 *
 * Each page groups N words together based on combineTokensWithinMilliseconds.
 * Returns pages with: text, startMs, durationMs, tokens[{text, fromMs, toMs}]
 */
export const useCaptionPages = (
  captions: Caption[],
  combineMs: number = 800
) => {
  return useMemo(() => {
    if (!captions || captions.length === 0) return [];

    // Convert our Caption format to Remotion's expected format
    const remotionCaptions = captions.map((c) => ({
      text: c.text,
      startMs: c.startMs,
      endMs: c.endMs,
      timestampMs: null as number | null,
      confidence: null as number | null,
    }));

    // One group per sentence: a page never holds the end of one sentence and the start of the next
    // ("CITY. THE"), so page breaks follow the spoken rhythm.
    const groups: (typeof remotionCaptions)[] = [[]];
    for (const c of remotionCaptions) {
      const g = groups[groups.length - 1];
      const prev = g[g.length - 1];
      // A bare number glues to the word before it ("GTA" + "6." -> "GTA 6."), so a page never ends on "GTA"
      // with the number alone on the next page (seen with the slower cloned voice, 2026-11-16).
      if (prev && /^\s*\d+[.!?,:]?["')]?\s*$/.test(c.text) && /[A-Za-z]$/.test(prev.text.trim())) {
        prev.text = `${prev.text.trimEnd()} ${c.text.trim()}`;
        prev.endMs = c.endMs;
      } else {
        g.push(c);
      }
      if (/[.!?…:]["')]?$/.test(c.text.trim())) groups.push([]);
    }
    return groups
      .filter((g) => g.length > 0)
      .flatMap(
        (g) => createTikTokStyleCaptions({ captions: g, combineTokensWithinMilliseconds: combineMs }).pages
      );
  }, [captions, combineMs]);
};
