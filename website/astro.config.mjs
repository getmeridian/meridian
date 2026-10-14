import fs from 'node:fs';
import path from 'node:path';
import { defineConfig } from 'astro/config';
import { unified } from '@astrojs/markdown-remark';
import sitemap from '@astrojs/sitemap';
import starlight from '@astrojs/starlight';
import starlightLinksValidator from 'starlight-links-validator';
import { remarkAppList } from './src/lib/apps-list.mjs';
import { remarkDiagrams } from './src/lib/diagrams.mjs';
import { DOCS_SIDEBAR, DOCS_SLUGS } from './src/lib/docs-sidebar.mjs';

const LOCALES = ['en', 'ru', 'fa', 'zh'];
const SITEMAP_EXCLUDED = ['/docs/', '/studio/', '/demo/'];

// A page missing from the sidebar list would build but stay unreachable from the navigation.
for (const locale of LOCALES) {
  const pages = fs
    .readdirSync(path.resolve('src/content/docs', locale))
    .filter((name) => /^[a-z].*\.md$/.test(name))
    .map((name) => name.slice(0, -3));
  const missing = pages.filter((slug) => !DOCS_SLUGS.includes(slug));
  const absent = DOCS_SLUGS.filter((slug) => !pages.includes(slug));
  if (missing.length || absent.length) {
    throw new Error(
      `src/content/docs/${locale}/ does not match src/lib/docs-sidebar.mjs` +
        (missing.length ? `; not in the sidebar: ${missing.join(', ')}` : '') +
        (absent.length ? `; no such page: ${absent.join(', ')}` : ''),
    );
  }
}

// Docs used to live under /docs/<locale>/<page>/; every old address keeps working.
const redirects = Object.fromEntries(
  LOCALES.flatMap((locale) => [
    [`/docs/${locale}`, `/${locale}/getting-started/`],
    ...DOCS_SLUGS.map((slug) => [`/docs/${locale}/${slug}`, `/${locale}/${slug}/`]),
  ]),
);

// Astro builds every Markdown file under src/pages, including the folder notes for contributors.
const dropContributorNotes = {
  name: 'drop-contributor-notes',
  hooks: {
    'astro:build:done': ({ dir }) => {
      const walk = (folder) => {
        for (const entry of fs.readdirSync(folder, { withFileTypes: true })) {
          const full = path.join(folder, entry.name);
          if (!entry.isDirectory()) continue;
          if (entry.name === 'CLAUDE') fs.rmSync(full, { recursive: true, force: true });
          else walk(full);
        }
      };
      walk(new URL(dir).pathname);
    },
  },
};

export default defineConfig({
  site: 'https://getmeridian.org',
  output: 'static',
  redirects,
  integrations: [
    dropContributorNotes,
    starlight({
      title: 'Meridian',
      description: 'Deploy censorship-resistant proxy servers with one command, hardened by default.',
      logo: { src: './src/assets/logo.png', alt: '' },
      favicon: '/favicon.svg',
      defaultLocale: 'en',
      locales: {
        en: { label: 'English', lang: 'en' },
        ru: { label: 'Русский', lang: 'ru' },
        fa: { label: 'فارسی', lang: 'fa', dir: 'rtl' },
        zh: { label: '中文', lang: 'zh-CN' },
      },
      social: [{ icon: 'github', label: 'GitHub', href: 'https://github.com/getmeridian/meridian' }],
      editLink: { baseUrl: 'https://github.com/getmeridian/meridian/edit/main/website/' },
      lastUpdated: true,
      sidebar: DOCS_SIDEBAR,
      customCss: ['./src/styles/fonts.css', './src/styles/starlight.css'],
      components: {
        SocialIcons: './src/components/starlight/SocialIcons.astro',
        PageTitle: './src/components/starlight/PageTitle.astro',
        MarkdownContent: './src/components/starlight/MarkdownContent.astro',
      },
      routeMiddleware: './src/lib/docs-route.ts',
      head: [
        {
          // Landing and docs share one theme. The language choice is only written when the
          // reader picks a language (src/i18n/CLAUDE.md); reading a docs page isn't a choice.
          tag: 'script',
          content:
            "try{var s=localStorage,t=s.getItem('meridian-theme');if(t&&!s.getItem('starlight-theme'))s.setItem('starlight-theme',t)}catch(e){}",
        },
        { tag: 'meta', attrs: { name: 'theme-color', content: '#FDFAF6', media: '(prefers-color-scheme: light)' } },
        { tag: 'meta', attrs: { name: 'theme-color', content: '#1C1E28', media: '(prefers-color-scheme: dark)' } },
      ],
      disable404Route: true,
      credits: false,
      // Blog, demo, and other custom pages live outside Starlight, so the validator cannot see them.
      plugins: [starlightLinksValidator({ exclude: ['/blog/**', '/demo/', '/studio/'] })],
    }),
    sitemap({
      // Redirects and tools are not pages to index; Astro's own redirect stubs are already left out.
      filter: (page) => !page.includes('/CLAUDE') && !SITEMAP_EXCLUDED.includes(new URL(page).pathname),
      // Same language tags as Starlight's `lang` above, so hreflang matches each page's <html lang>.
      i18n: {
        defaultLocale: 'en',
        locales: {
          en: 'en',
          ru: 'ru',
          fa: 'fa',
          zh: 'zh-CN',
        },
      },
    }),
  ],
  build: {
    format: 'directory',
  },
  vite: {
    // tokens.css writes colors as light-dark(); Lightning CSS lowers it (and other
    // newer syntax) for browsers older than these, e.g. Safari on iOS 16.
    build: { cssTarget: ['chrome107', 'edge107', 'firefox104', 'safari16'] },
  },
  // Prefetch a page only when the reader points at its link; 'viewport' downloaded every
  // sidebar page on each visit, which costs real money on metered connections.
  prefetch: {
    prefetchAll: true,
    defaultStrategy: 'hover',
  },
  markdown: {
    // Mermaid blocks become their checked-in SVGs before Expressive Code sees them;
    // `<!-- apps -->` becomes the app list from src/data/apps.json.
    processor: unified({ remarkPlugins: [remarkDiagrams, remarkAppList] }),
    shikiConfig: {
      themes: {
        light: 'github-light',
        dark: 'github-dark',
      },
      defaultColor: false,
    },
  },
});
