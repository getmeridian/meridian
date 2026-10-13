# styles — Design token system

## Design decisions

**Tokens in `tokens.css`** — all colors, typography, shadows, radii as CSS custom properties. No hardcoded values elsewhere.

**Light-first, warm palette** — default is warm cream (#FDFAF6). Dark mode uses warm charcoal (#1C1E28), never pure black. Both modes maintain the brand warmth.

**Theme override via `data-theme`** — `data-theme="dark"` or `data-theme="light"` on `<html>` overrides OS preference. No attribute = follow OS. ThemeToggle component in the nav manages this. FOUC prevented by early `<script>` in `<head>` that reads localStorage before paint.

**Three font families** — Fraunces (serif headings, editorial feel), Source Sans 3 (body), JetBrains Mono (code). All self-hosted as variable WOFF2. `font-display: swap` prevents invisible text.

**Color naming is generic** — `--ac` (accent), `--t1` (text primary), `--s1` (surface). Requires familiarity but keeps the token set small and composable.

**Starlight theme** — `starlight.css` maps the palette onto Starlight's `--sl-color-*` scale (white = strongest text, black = page background, inverted for light mode) and applies Fraunces to headings. Change both files when the palette moves.

## What's done well

- **Dark mode is a full token remap** — every property has a dark variant. Dual-selector approach: `@media (prefers-color-scheme: dark) :root:not([data-theme="light"])` + `[data-theme="dark"]`.
- **Code surface tokens** — `--code-bg` and `--code-br` give code blocks distinct contrast from page background in both modes.
- **Global focus states** — 2px accent outline on all interactive elements. Defined once in `global.css`.

## Pitfalls

- **No manual dark mode toggle** — RESOLVED. ThemeToggle component cycles system/light/dark.
- **No explicit z-index scale** — sticky elements may conflict as layering increases.
- **Shadows hardcode rgba** — should derive from tokens for true dark mode adaptability.
