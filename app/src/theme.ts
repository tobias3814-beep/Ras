/** Colour themes. The colours live in index.css as CSS variables per `data-theme`; index.html applies the saved theme
 *  before anything renders. Charts and the 3D figure read the variables when they're created, so switching theme
 *  reloads the window once to redraw them. */
export const THEMES = [
  { id: "graphite", name: "Graphite (bot console)", swatch: ["#090c11", "#4cc2ff"] },
  { id: "light-purple", name: "Light purple", swatch: ["#f6f2fd", "#7c3aed"] },
  { id: "light-blue", name: "Light blue", swatch: ["#f1f6fd", "#1d4ed8"] },
  { id: "purple-black", name: "Purple / black", swatch: ["#07040d", "#a78bfa"] },
  { id: "red-black", name: "Red / black", swatch: ["#080404", "#f87171"] },
  { id: "blue-black", name: "Blue / black", swatch: ["#04060d", "#60a5fa"] },
  { id: "cyan-black", name: "Cyan / black (classic)", swatch: ["#05080d", "#3fe0ff"] },
] as const;
export const THEME_KEY = "ras.theme";

export function currentTheme(): string {
  return document.documentElement.dataset.theme || "graphite";
}

export function setTheme(id: string): void {
  try { localStorage.setItem(THEME_KEY, id); } catch { /* storage blocked: applies until reload */ }
  try { sessionStorage.setItem("ras.view", "settings"); } catch { /* lands on Station instead */ }
  document.documentElement.dataset.theme = id;
  location.reload(); // charts + 3D figure pick up the new colours
}

/** A theme colour as a hex string, e.g. css("--chart-gain"). */
export const css = (name: string): string => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
