/**
 * The site's locales and their per-locale facts. Starlight's `locales` in
 * astro.config.mjs and the early head script in Base.astro list the same four.
 */
export type Locale = 'en' | 'ru' | 'fa' | 'zh';
export const LOCALES: readonly Locale[] = ['en', 'ru', 'fa', 'zh'];

export const LOCALE_INFO: Record<Locale, { label: string; short: string; lang: string; og: string; hreflang: string }> = {
  en: { label: 'English', short: 'EN', lang: 'en', og: 'en_US', hreflang: 'en' },
  ru: { label: 'Русский', short: 'RU', lang: 'ru', og: 'ru_RU', hreflang: 'ru' },
  fa: { label: 'فارسی', short: 'فا', lang: 'fa', og: 'fa_IR', hreflang: 'fa' },
  zh: { label: '中文', short: '中文', lang: 'zh-CN', og: 'zh_CN', hreflang: 'zh-CN' },
};

export function isLocale(value: unknown): value is Locale {
  return typeof value === 'string' && (LOCALES as readonly string[]).includes(value);
}

export function dirOf(locale: Locale): 'ltr' | 'rtl' {
  return locale === 'fa' ? 'rtl' : 'ltr';
}

/** Path of a locale's landing page: `/` for English, `/ru/` and so on otherwise. */
export function homeOf(locale: Locale): string {
  return locale === 'en' ? '/' : `/${locale}/`;
}
