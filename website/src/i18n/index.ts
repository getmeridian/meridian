/**
 * Meridian i18n — client-side translation system.
 *
 * How it works:
 * 1. English content is rendered at build time in HTML
 * 2. Elements with data-t="key" get text swapped client-side
 * 3. Language is auto-detected from browser, persisted in localStorage
 * 4. LanguagePicker component lets users switch manually
 *
 * To add a new translatable element:
 *   <span data-t="key">English fallback</span>
 */

export type Locale = 'en' | 'ru' | 'fa' | 'zh';
export const LOCALES: Locale[] = ['en', 'ru', 'fa', 'zh'];

const STORAGE_KEY = 'meridian-lang';

/** Detect preferred locale from browser or localStorage. */
export function detectLocale(): Locale {
  const saved = localStorage.getItem(STORAGE_KEY);
  if (saved && LOCALES.includes(saved as Locale)) return saved as Locale;

  const bl = (navigator.language || '').slice(0, 2);
  if (bl === 'ru') return 'ru';
  if (bl === 'fa') return 'fa';
  if (bl === 'zh') return 'zh';
  return 'en';
}

/** Apply translations to all data-t elements on the page. */
export function setLang(lang: Locale, translations: Record<string, Record<string, string>>) {
  // Set HTML attributes
  document.documentElement.lang = lang;
  document.documentElement.dir = lang === 'fa' ? 'rtl' : 'ltr';

  // Swap text content
  document.querySelectorAll('[data-t]').forEach((el) => {
    const key = (el as HTMLElement).dataset.t!;
    const text = translations[lang]?.[key];
    if (text) {
      el.innerHTML = text;
    } else if (lang === 'en') {
      // English is the build-time content — no swap needed
    }
  });

  // Swap placeholder attributes
  document.querySelectorAll('[data-t-placeholder]').forEach((el) => {
    const key = (el as HTMLElement).dataset.tPlaceholder!;
    const text = translations[lang]?.[key];
    if (text) {
      (el as HTMLInputElement).placeholder = text;
    }
  });

  // Update picker active state
  document.querySelectorAll('.lang-picker__btn').forEach((btn) => {
    const btnLang = (btn as HTMLElement).dataset.lang;
    btn.classList.toggle('lang-picker__btn--active', btnLang === lang);
  });

  // Persist
  localStorage.setItem(STORAGE_KEY, lang);

  // Update document title
  const pageTitle = translations[lang]?.['page.title'];
  if (pageTitle) document.title = pageTitle;
}
