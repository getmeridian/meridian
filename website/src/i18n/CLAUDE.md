# i18n — Client-side translation system

## Design decisions

**Asymmetric by design** — English is baked into HTML at build time; other languages swap text client-side via `data-t` attributes, so no English dictionary ships in JS. Components and the PWA follow the same model.

**Detection cascade** — `detectLocale()`: localStorage → browser language → English fallback. URL path locale (`/ru/`, `/fa/`, `/zh/`) is detected by the early `<head>` script in Base layout and persisted to localStorage before this code runs.

**Build-time helper** — `createT()` in `t.ts` provides a translation function for Astro component frontmatter. Used for locale-specific pages where DOM swapping isn't sufficient (e.g., localized landing pages).

## What's done well

- **`setLang()` handles everything** — text swap, placeholder swap, `dir` attribute, `lang` attribute, picker state, localStorage persistence, and document title. One call, full i18n.
- **RTL is first-class** — Farsi (`fa`) sets `dir="rtl"` on `<html>`. Components that need directional overrides use `[dir="rtl"]` selectors.

## Pitfalls

- **Translation keys must match `data-t` attributes exactly** — `translations.ts` keys are manually maintained. A typo in either side fails silently (English stays visible).
- **EN reload required** — switching back to English reloads the page because English text is in the build-time HTML, not in a translation dict. Non-EN to non-EN is a seamless client-side swap.
- **`data-t-placeholder`** — separate attribute for `<input>` placeholders. Easy to forget when adding translatable form elements.
- **`innerHTML` for translated text** — `setLang()` uses `innerHTML` (not `textContent`) because some translations contain inline HTML (`<em>`, `<a>`, `<strong>`). Translation values must be trusted.
