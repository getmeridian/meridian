import fs from 'node:fs';
import path from 'node:path';
import { defineConfig } from 'astro/config';
import { unified } from '@astrojs/markdown-remark';
import sitemap from '@astrojs/sitemap';
import starlight from '@astrojs/starlight';
import starlightLinksValidator from 'starlight-links-validator';

const LOCALES = ['en', 'ru', 'fa', 'zh'];
const DOCS_DIR = path.resolve('src/content/docs');

// Sidebar order and sections come from each English page's `order` and `section` frontmatter;
// Starlight resolves the same slugs in every locale.
function englishDocs() {
  return fs
    .readdirSync(path.join(DOCS_DIR, 'en'))
    .filter((name) => /^[a-z].*\.md$/.test(name))
    .map((name) => {
      const front = fs.readFileSync(path.join(DOCS_DIR, 'en', name), 'utf8').split('---')[1] ?? '';
      const field = (key) => front.match(new RegExp(`^${key}:\\s*(.+)$`, 'm'))?.[1].trim();
      return { slug: name.slice(0, -3), order: Number(field('order') ?? 99), section: field('section') ?? 'guides' };
    })
    .sort((a, b) => a.order - b.order);
}

const SECTIONS = [
  { key: 'guides', label: 'Guides', translations: { ru: 'Руководства', fa: 'راهنماها', 'zh-CN': '指南' } },
  { key: 'reference', label: 'Reference', translations: { ru: 'Справочник', fa: 'مرجع', 'zh-CN': '参考' } },
];
const docs = englishDocs();
const sidebar = SECTIONS.map(({ key, label, translations }) => ({
  label,
  translations,
  items: docs.filter((doc) => doc.section === key).map((doc) => doc.slug),
}));

// Docs used to live under /docs/<locale>/<page>/; every old address keeps working.
const redirects = Object.fromEntries(
  LOCALES.flatMap((locale) => [
    [`/docs/${locale}`, `/${locale}/getting-started/`],
    ...docs.map((doc) => [`/docs/${locale}/${doc.slug}`, `/${locale}/${doc.slug}/`]),
  ]),
);

// Mermaid blocks become plain <pre class="mermaid"> before Expressive Code sees them;
// the MarkdownContent override renders them in the browser.
function remarkMermaid() {
  const escape = (text) => text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const visit = (node) => {
    node.children?.forEach((child, index) => {
      if (child.type === 'code' && child.lang === 'mermaid') {
        node.children[index] = { type: 'html', value: `<pre class="mermaid">${escape(child.value)}</pre>` };
      } else {
        visit(child);
      }
    });
  };
  return visit;
}

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
      favicon: '/img/favicon-32.png',
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
      sidebar,
      customCss: ['./src/styles/fonts.css', './src/styles/starlight.css'],
      components: {
        SocialIcons: './src/components/starlight/SocialIcons.astro',
        PageTitle: './src/components/starlight/PageTitle.astro',
        MarkdownContent: './src/components/starlight/MarkdownContent.astro',
      },
      routeMiddleware: './src/lib/docs-route.ts',
      head: [
        {
          // Landing and docs share one theme and language choice.
          tag: 'script',
          content:
            "try{var s=localStorage,t=s.getItem('meridian-theme');if(t&&!s.getItem('starlight-theme'))s.setItem('starlight-theme',t);s.setItem('meridian-lang',document.documentElement.lang.slice(0,2))}catch(e){}",
        },
      ],
      disable404Route: true,
      credits: false,
      // Blog, demo, and other custom pages live outside Starlight, so the validator cannot see them.
      plugins: [starlightLinksValidator({ exclude: ['/blog/**', '/demo/', '/studio/'] })],
    }),
    sitemap({
      filter: (page) => !page.includes('/CLAUDE'),
      i18n: {
        defaultLocale: 'en',
        locales: {
          en: 'en-US',
          ru: 'ru-RU',
          fa: 'fa-IR',
          zh: 'zh-Hans',
        },
      },
    }),
  ],
  build: {
    format: 'directory',
  },
  prefetch: {
    prefetchAll: true,
    defaultStrategy: 'viewport',
  },
  markdown: {
    processor: unified({ remarkPlugins: [remarkMermaid] }),
    shikiConfig: {
      themes: {
        light: 'github-light',
        dark: 'github-dark',
      },
      defaultColor: false,
    },
  },
});
