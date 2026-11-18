# components — Astro UI components

## Design decisions

**Stateless + presentational** — components take props (strings already translated by the page) and render HTML; interactivity is small vanilla scripts, and everything reads without JavaScript.

**One landing** — `Landing.astro` renders the home page for any `locale`; `pages/index.astro` and `pages/[locale]/index.astro` only pass it. The connection atlas starts as a cacheable engraving. Only a visible atlas with motion allowed loads the orthographic canvas; the turn runs once and Replay is explicit. Keep the printed and moving bearings aligned. The two recorded demos precede setup, with the video beside the commands and advanced material in native disclosures.

**Shared pieces** — `Nav` mirrors Starlight's header (height, logo, link style) and uses a `<details>` mobile menu; `LanguagePicker` is plain links; `SectionHead`, `.btn`, `.card`, `.pill` (global.css) open and style every section; `Footer` is the one footer; `blog/` holds `BlogCard`, `BlogListing`, and the post helpers.

**Copy-to-clipboard** — `copy.ts` is the one implementation (Clipboard API, `execCommand` fallback, `[data-copied]`), used by `CopyButton` and the command builder; Markdown code blocks get Expressive Code's button.

**Theme-following figures** — `ArchitectureDiagram` is inline SVG drawn with tokens, so it follows light, dark, and the reader's override.

**Starlight overrides** — `starlight/` replaces `SocialIcons` (Blog and Demo links), `PageTitle` (Copy page plus a native disclosure for Markdown and assistant links; without JavaScript only the working links appear), and `MarkdownContent` (adds `Mermaid`, the layout for build-rendered diagrams; it ships no script). Wrap Starlight's default component instead of copying it; visual alignment with the site lives in `src/styles/starlight.css`.

## What's done well

- **CommandBuilder can't drift** — tabs, commands, and flags live in `src/data/command-builder.json`; `tests/test_website_command_builder.py` checks them against the live CLI and the palettes against `PAGE_PALETTES`. Its script gets only the current locale's strings; values are shell-quoted. Show the builder and its copy controls only after initialization; the fallback is the localized CLI reference.
- **Fast animations, always visible** — no scroll-reveal or hover-only controls; the theme toggle stays hidden until its script runs.

## Pitfalls

- **Keep fragment targets inside disclosures** — browsers open the enclosing details when a reader visits `#builder-section` or `#connect`. Keep `#video` on the visible walkthrough for links from the README.
- **Bump the builder's storage key** (`meridian-builder-v2`) when field ids change; it writes only after the reader edits something.
- **BEM naming** — `block__element` classes (`nav__inner`, `builder__tab`); new components follow suit.
- **Use logical properties for direction** — a `row-reverse` under `dir="rtl"` flips the layout back to LTR.
