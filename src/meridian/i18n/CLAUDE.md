# i18n — CLI message catalog

## Design decisions

**Flat JSON per locale** — `locales/<code>.json` maps dotted keys to text with `{name}` placeholders; English is the source and the fallback for a missing key. Locales match the website: en, ru, fa, zh.
**Explicit beats guessed** — `--lang` (`request_locale`) > `MERIDIAN_LANG` > `preferences.json` in the Meridian home (saved by the wizard's language step) > ask, preselected from `LC_ALL`/`LC_MESSAGES`/`LANG`.
**Reply words are catalog entries** — `word.*` values list typed answers separated by `|` (first one is shown); `words()` always accepts English too, so a wrong keyboard layout never strands anyone.
**Renderers translate, events don't** — progress events and JSON keep English (machine contract); `apply_progress` labels come from `progress.kind.<kind>` and step names from `step.<slug>` via `step_label()`. The root callback sets the language for human output only.
**No markup in catalogs** — callers escape catalog text and add Rich styles themselves, so a translator can never break rendering.

## What's done well
- Individual connection and format headings and unavailable labels match across all four CLI locales.
- Notice preview, expiry, confirmation, clearing, and content-free timeline labels exist in all four CLI locales; operator text remains verbatim.
- Owner-contact reviews, clearing, validation, and read-only console guidance have matching four-locale keys; operator-supplied instructions stay verbatim.
- Private note/tag labels, bounds, clearing guidance, and scope disclosures have matching English, Russian, Persian, and Chinese catalog entries.

- `tests/test_i18n.py` enforces key order/parity, identical placeholders, no reply-word collisions, and that every literal key used in code exists.

## Pitfalls
- Server allowance text must disclose accepted upload/download coverage and excluded overhead, host downloads, and relay forwarding in every locale; unmeasured servers never display a zero usage figure.

- Never make a reply word equal a value the user types at a choice prompt (`exit`, `relay`, `gateway`, `control`); navigation words are matched first.
- Persian users may type Persian digits or the Arabic comma `،`; parsing goes through `int()` and normalizes the comma.
- Keep commands, protocol names, `local`, and device-cap `default` untranslated.
- Wrap names in right-to-left text with `isolate()`, never literal U+2068/U+2069: it marks only Persian output, and plain terminals printed the marks around node names in every language.
