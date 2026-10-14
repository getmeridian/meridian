# i18n — Website strings for the landing and the site shell

## Design decisions

**Static per locale** — the landing renders at `/`, `/ru/`, `/fa/`, `/zh/` from `landing/<locale>.ts` at build time; nothing about it is translated in the browser, so it is complete without JavaScript and crawlable.

**Typed parity** — `landing/en.ts` and `shell/en.ts` are the references; every other locale is typed `LandingStrings`/`ShellStrings`, so a missing or extra key fails `astro check`. Values may hold trusted inline HTML; `{base}` becomes the locale's docs prefix.

**Shell swap only where no locale version exists** — blog and 404 load `swap.ts` (shell dictionaries only, a few KB): `data-t` text, `data-t-label` aria-labels, docs links, and `lang`/`dir` on each `data-i18n-scope` element. The document itself stays English and LTR.

**Explicit choice wins** — `meridian-lang` is written only when the reader clicks a language link; `preferredLocale()` falls back to the browser language. `locales.ts` holds the locale list, `lang` (zh is `zh-CN`, as in Starlight), og locale, and hreflang.

## What's done well

- **RTL is first-class** — Farsi sets `dir="rtl"` on `<html>` (landing) or the swapped scope; components use logical properties (`inset-inline-*`, `border-inline-start`) instead of `[dir=rtl]` flips.

## Pitfalls

- **Never ship `landing/*` to the client** — import it only in Astro frontmatter; `swap.ts` must import `shell/` alone.
- **Adding a locale** touches `locales.ts`, both dictionaries, Starlight's `locales`, and the `/` redirect list in `Landing.astro`.
- **Keep commands and flags untranslated** — `meridian deploy`, `--domain`, and protocol names stay Latin in every locale.
