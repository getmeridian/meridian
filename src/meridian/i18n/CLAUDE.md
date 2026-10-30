# i18n — CLI message catalog

## Design decisions

**Flat JSON per locale** — `locales/<code>.json` maps dotted keys to text with `{name}` placeholders; English is the source and the fallback for a missing key. Locales match the website: en, ru, fa, zh.
**Explicit beats guessed** — `--lang` (`request_locale`) > `MERIDIAN_LANG` > `preferences.json` in the Meridian home (saved by the wizard's language step) > ask, preselected from `LC_ALL`/`LC_MESSAGES`/`LANG`.
**Reply words are catalog entries** — `word.*` values list typed answers separated by `|` (first one is shown); `words()` always accepts English too, so a wrong keyboard layout never strands anyone.
**Renderers translate, events don't** — progress events and JSON keep English (machine contract); `apply_progress` labels come from `progress.kind.<kind>` and step names from `step.<slug>` via `step_label()`. The root callback sets the language for human output only.
**No markup in catalogs** — callers escape catalog text and add Rich styles themselves, so a translator can never break rendering.

## What's done well
- Private note/tag labels, bounds, clearing guidance, and scope disclosures have matching English, Russian, Persian, and Chinese catalog entries.

- `tests/test_i18n.py` enforces key order/parity, identical placeholders, no reply-word collisions, and that every literal key used in code exists.

## Pitfalls

- Never make a reply word equal a value the user types at a choice prompt (`exit`, `relay`, `gateway`, `control`); navigation words are matched first.
- Persian users may type Persian digits or the Arabic comma `،`; parsing goes through `int()` and normalizes the comma.
- Keep commands, protocol names, `local`, and device-cap `default` untranslated.
