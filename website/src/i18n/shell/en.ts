/**
 * Site shell strings — nav, footer, and the 404 page — English reference.
 *
 * Landing pages render these at build time. Pages without a static locale version
 * (blog, 404) ship the shell dictionaries and swap `data-t` text in the browser;
 * nothing else ships as a dictionary. Values are plain text.
 */
export const en = {
  'skip': 'Skip to content',
  'nav.home': 'Meridian home',
  'nav.main': 'Main navigation',
  'nav.docs': 'Docs',
  'nav.blog': 'Blog',
  'nav.demo': 'Demo',
  'nav.menu': 'Menu',
  'nav.language': 'Language',
  'nav.theme': 'Dark theme',
  'footer.tagline': 'Open source. No tracking. No accounts.',
  'footer.issues': 'Issues',
  'footer.rss': 'RSS',
  'notFound.title': 'Page not found',
  'notFound.text': 'This page doesn’t exist, or it has moved.',
  'notFound.home': 'Home',
  'notFound.docs': 'Read the docs',
};

export type ShellKey = keyof typeof en;
export type ShellStrings = Record<ShellKey, string>;
