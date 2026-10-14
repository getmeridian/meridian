# components — Astro UI components

## Design decisions

**Stateless + presentational** — components take props (strings already translated by the page) and render HTML; interactivity is small vanilla scripts, and everything reads without JavaScript.

**One landing** — `Landing.astro` renders the home page for any `locale`; `pages/index.astro` and `pages/[locale]/index.astro` only pass it.

**Shared pieces** — `Nav` mirrors Starlight's header (height, logo, link style) and uses a `<details>` mobile menu; `LanguagePicker` is plain links; `SectionHead`, `.btn`, `.card`, `.pill` (global.css) open and style every section; `Footer` is the one footer; `blog/` holds `BlogCard`, `BlogListing`, and the post helpers.

**Copy-to-clipboard** — `copy.ts` is the one implementation (Clipboard API, `execCommand` fallback, `[data-copied]`), used by `CopyButton` and the command builder; Markdown code blocks get Expressive Code's button.

**Theme-following figures** — `ArchitectureDiagram` is inline SVG drawn with tokens, so it follows light, dark, and the reader's override.

**Starlight overrides** — `starlight/` replaces `SocialIcons` (Blog and Demo links), `PageTitle` (Copy page, View as Markdown, Open in ChatGPT/Claude), and `MarkdownContent` (adds `Mermaid`, the layout for build-rendered diagrams; it ships no script). Wrap Starlight's default component instead of copying it; visual alignment with the site lives in `src/styles/starlight.css`.

## What's done well

- **CommandBuilder can't drift** — tabs, commands, and flags live in `src/data/command-builder.json`; `tests/test_website_command_builder.py` checks them against the live CLI and the palettes against `PAGE_PALETTES`. Its script gets only the current locale's strings; values are shell-quoted.
- **Fast animations, always visible** — no scroll-reveal or hover-only controls; the theme toggle stays hidden until its script runs.

## Pitfalls

- **Bump the builder's storage key** (`meridian-builder-v2`) when field ids change; it writes only after the reader edits something.
- **BEM naming** — `block__element` classes (`nav__inner`, `builder__tab`); new components follow suit.
- **Use logical properties for direction** — a `row-reverse` under `dir="rtl"` flips the layout back to LTR.
