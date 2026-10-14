import type { Locale } from '../locales';
import { en, type ShellStrings } from './en';
import { fa } from './fa';
import { ru } from './ru';
import { zh } from './zh';

export type { ShellKey, ShellStrings } from './en';
export const shell: Record<Locale, ShellStrings> = { en, ru, fa, zh };
