import type { MutableRefObject } from "react";

export type AvatarState = "idle" | "listening" | "thinking" | "speaking" | "alert";
export type AvatarTone = "normal" | "warning" | "breach";

/** What the avatar gets from the voice panel.
 *  `level` is a ref (0..1 voice amplitude, ~20 updates/s) so avatars animate without React re-renders. */
export type AvatarProps = { state: AvatarState; tone: AvatarTone; level: MutableRefObject<number>; reply?: string };

/** Alert tint in the active theme: accent normally, amber for a warning, red for a risk breach. */
export const toneColor = (tone: AvatarTone): string =>
  getComputedStyle(document.documentElement).getPropertyValue(
    tone === "normal" ? "--color-hud-accent" : tone === "warning" ? "--color-hud-amber" : "--color-hud-red").trim();
