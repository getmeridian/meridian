# components — Astro UI components

## Design decisions

**Stateless + presentational** — most components take props and render HTML. Interactivity is minimal vanilla JS (`is:inline`). No framework state.

**i18n via `data-t` attributes** — all visible text uses `data-t="key"`. Consumer JS reads and swaps. Avoids baking translations into component markup.

**Copy-to-clipboard pattern** — `CopyButton.astro` shared by `CodeBlock` and `CommandBuilder`. Clipboard API with `execCommand` fallback. State tracked via `[data-copied]` CSS attribute.

**Fast animations, always visible** — CSS transitions should feel instant (most under 100ms; decorative transforms like accordion chevron rotation can be slightly longer). Never hide interactive elements behind hover — components must be visible at all times. Hover states are for visual feedback, not for revealing content.

**Starlight overrides** — `starlight/` replaces `SocialIcons` (Blog and Demo links), `PageTitle` (Copy page, View as Markdown, Open in ChatGPT/Claude), and `MarkdownContent` (adds `Mermaid`). Wrap Starlight's default component instead of copying it.

## What's done well

- **Accordion `> summary` rule** — child combinator prevents style leaking into nested `<details>`. Learned from a real bug where parent chevrons appeared on nested toggles.
- **RTL-aware** — Callout border flips, Timeline reverses, code blocks force LTR via `unicode-bidi: embed`.
- **CommandBuilder** — 6-tab interactive tool, no framework. Config objects drive both UI and command string. localStorage persistence survives page reload.

## Pitfalls

- **CommandBuilder localStorage has no schema versioning** — field renames break saved state silently.
- **BEM naming** — Nav uses BEM (`nav__inner`, `nav__links`). Other components don't. Be consistent when adding new ones.
