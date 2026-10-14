# styles — Design token system

## Design decisions

**Tokens in `tokens.css`** — colors, type, shadows, radii, and layout (`--page-max`, `--prose-max`, `--nav-h`, `--section-y`) as custom properties; no hard-coded colors elsewhere.

**One value per color** — each token is `light-dark(light, dark)`; `color-scheme` picks the side (OS by default, `data-theme` on `<html>` overrides). `vite.build.cssTarget` in `astro.config.mjs` makes Lightning CSS lower `light-dark()` for older browsers (iOS 16).

**Light-first, warm palette** — cream (#FDFAF6) and warm charcoal (#1C1E28), never pure black. `--ac` gold is a fill and decoration color; text uses `--ac-text`, and text on a gold fill uses `--on-ac`.

**Shared building blocks in `global.css`** — `.container`, `.section`, `.eyebrow` + `.section-head`, `.btn--primary/--secondary`, `.card`, `.pill`; landing, blog, and 404 reuse them instead of per-page copies.

**Fonts are script subsets** — `fonts.css` serves Fraunces (headings), Source Sans 3 (body), and JetBrains Mono (code) as Latin and Cyrillic files with `unicode-range`; Russian headings use Source Serif 4 (Fraunces has no Cyrillic), Farsi uses Vazirmatn, Chinese the system CJK fonts. `scripts/subset_fonts.py` rebuilds them from the upstream GitHub releases; licenses ship in `public/fonts/licenses/`.

**Docs share the look** — `starlight.css` maps the palette onto Starlight's `--sl-color-*` scale (inverted for light), heading families and weights, and the header's logo size and translucency. `prose.css` gives blog posts the docs' text color and lends Expressive Code the same `--sl-color-*` values. Change all three when the palette moves.

## What's done well

- **AA contrast by construction** — `--t1..--t3`, `--t-body`, `--ac-text`, `--ok`, `--err` reach 4.5:1 on `--bg`, `--s1`, `--s2`, and `--code-bg` in both themes; Starlight's text steps use the same hex values.
- **Per-language type** — `:lang(ru|fa|zh)` swaps `--heading`/`--body`/`--label-font` at element level, so a nav swapped to Farsi on an English page gets Vazirmatn; fa/zh drop faux italics, tracking, and uppercase.

## Pitfalls

- **Keep `light-dark()` lowerable** — only colors inside it; a browser without support and without lowering would drop every color.
- **Visible text in another script downloads its font** — the language picker uses system fonts so `/` doesn't fetch Vazirmatn and Cyrillic files for the words «Русский» and «فارسی».
- **The hero italic is hard-subset to ASCII** — italic accent words in other scripts render upright (`Hero.astro`); don't use `<em>` in headings elsewhere expecting Fraunces italic.
- **No explicit z-index scale** — the sticky header is 100, the skip link 200.
