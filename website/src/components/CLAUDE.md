# components — Astro UI components

## Design decisions

**Stateless + presentational** — most components take props and render HTML. Interactivity is minimal vanilla JS (`is:inline`). No framework state.

**Translatable text carries `data-t`** — see `src/i18n/CLAUDE.md`.

**Copy-to-clipboard pattern** — `CopyButton.astro` (used by `CodeBlock`) tries the Clipboard API, falls back to `execCommand`, and signals state with a `[data-copied]` attribute.

**Fast animations, always visible** — transitions feel instant (most under 100ms; decorative transforms may run longer); never hide interactive elements behind hover, which is for feedback only.

**Starlight overrides** — `starlight/` replaces `SocialIcons` (Blog and Demo links), `PageTitle` (Copy page, View as Markdown, Open in ChatGPT/Claude), and `MarkdownContent` (adds `Mermaid`). Wrap Starlight's default component instead of copying it.

## What's done well

- **RTL-aware** — Callout border flips, Timeline reverses, code blocks force LTR via `unicode-bidi: embed`.
- **CommandBuilder** — one config object per tab drives both the UI and the command string; choices persist in localStorage.

## Pitfalls

- **CommandBuilder localStorage has no schema versioning** — field renames break saved state silently.
- **BEM naming** — components use `block__element` classes (`nav__inner`, `accordion__trigger`); new ones follow suit.
