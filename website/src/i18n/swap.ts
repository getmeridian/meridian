/**
 * Client-side shell swap for pages without a static locale version (blog, 404).
 *
 * Content stays English; the nav, footer, and any `data-t` element take the reader's
 * language from the shell dictionaries, and each `data-i18n-scope` element gets the
 * matching `lang`/`dir` so fonts and direction follow. Landing pages never load this.
 */
import { LOCALE_INFO, dirOf, isLocale, type Locale } from './locales';
import { shell, type ShellKey } from './shell';

const STORAGE_KEY = 'meridian-lang';

/** Saved choice, else the browser's language, else English. */
export function preferredLocale(): Locale {
  let saved: string | null = null;
  try {
    saved = localStorage.getItem(STORAGE_KEY);
  } catch {
    // Storage can be blocked (Tor Browser, strict privacy modes); fall through.
  }
  if (isLocale(saved)) return saved;
  const browser = (navigator.language || '').slice(0, 2).toLowerCase();
  return isLocale(browser) ? browser : 'en';
}

export function swapShell(locale: Locale = preferredLocale()): void {
  if (locale === 'en') return;
  const dict = shell[locale];
  const key = (value: string | undefined) => (value && value in dict ? dict[value as ShellKey] : undefined);

  document.querySelectorAll<HTMLElement>('[data-t]').forEach((el) => {
    const text = key(el.dataset.t);
    if (text) el.textContent = text;
  });
  document.querySelectorAll<HTMLElement>('[data-t-label]').forEach((el) => {
    const text = key(el.dataset.tLabel);
    if (text) el.setAttribute('aria-label', text);
  });
  document.querySelectorAll<HTMLElement>('[data-i18n-scope]').forEach((el) => {
    el.lang = LOCALE_INFO[locale].lang;
    el.dir = dirOf(locale);
  });

  // Docs and home links follow the reader's language.
  document.querySelectorAll<HTMLAnchorElement>('a[data-docs-link]').forEach((a) => {
    a.href = `/${locale}/getting-started/`;
  });
  document.querySelectorAll<HTMLAnchorElement>('a[data-home-link]').forEach((a) => {
    a.href = `/${locale}/`;
  });
  document.querySelectorAll<HTMLAnchorElement>('a[data-lang-choice]').forEach((a) => {
    if (a.dataset.langChoice === locale) a.setAttribute('aria-current', 'true');
    else a.removeAttribute('aria-current');
  });

  const titleKey = key(document.body.dataset.titleKey);
  if (titleKey) document.title = `${titleKey} — Meridian`;
}
