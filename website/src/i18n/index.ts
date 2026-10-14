/**
 * Build-time translation helpers for Astro frontmatter. Nothing here ships to the
 * browser; the client-side shell swap lives in swap.ts.
 */
import { en, type LandingKey, type LandingStrings } from './landing/en';
import { fa } from './landing/fa';
import { ru } from './landing/ru';
import { zh } from './landing/zh';
import type { Locale } from './locales';
import { shell, type ShellKey } from './shell';

export * from './locales';
export type { LandingKey, ShellKey };

const landing: Record<Locale, LandingStrings> = { en, ru, fa, zh };

export type LandingT = (key: LandingKey) => string;

/** Landing strings for a locale; `{base}` in a value becomes the locale's docs prefix. */
export function landingT(locale: Locale): LandingT {
  const dict = landing[locale];
  return (key) => dict[key].replaceAll('{base}', `/${locale}`);
}

export function shellT(locale: Locale): (key: ShellKey) => string {
  return (key) => shell[locale][key];
}
