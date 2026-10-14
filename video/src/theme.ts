import { loadFont } from "@remotion/fonts";
import { staticFile } from "remotion";

// The website's dark palette (website/src/styles/tokens.css) and its self-hosted fonts.
export const color = {
  bg: "#1C1E28",
  band: "#20222D",
  surface: "#252836",
  surface2: "#2E3145",
  border: "#3A3E52",
  text: "#E4E2DE",
  body: "#C4C3C8",
  muted: "#A6A8B6",
  accent: "#D4A24E",
  accentSoft: "#D4A24E1F",
  ok: "#5CE88E",
  err: "#F06060",
  terminal: "#1E2030",
};

export const font = {
  sans: "'Source Sans 3', system-ui, sans-serif",
  heading: "'Fraunces', Georgia, serif",
  mono: "'JetBrains Mono', ui-monospace, Menlo, monospace",
};

export const FPS = 30;
export const WIDTH = 1920;
export const HEIGHT = 1080;

const LATIN =
  "U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308, U+0329, U+2000-206F, U+20AC, U+2122, U+2190-2195, U+2212, U+2215, U+FEFF, U+FFFD";
const CYRILLIC = "U+0301, U+0400-045F, U+0490-0491, U+04B0-04B1, U+2116";

// Same faces and ranges as website/src/styles/fonts.css.
const faces: [string, string, string, string][] = [
  ["JetBrains Mono", "fonts/jetbrains-mono-latin.woff2", "400 600", LATIN],
  ["JetBrains Mono", "fonts/jetbrains-mono-cyrillic.woff2", "400 600", CYRILLIC],
  ["Source Sans 3", "fonts/source-sans-3-latin.woff2", "400 700", LATIN],
  ["Fraunces", "fonts/fraunces-latin.woff2", "400 600", LATIN],
];
for (const [family, file, weight, unicodeRange] of faces) {
  loadFont({ family, url: staticFile(file), weight, unicodeRange });
}
