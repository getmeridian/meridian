/**
 * The docs as Markdown for language models — the one place that bundles them.
 *
 * - `/llms-full.txt`: every English page in sidebar order, links absolute.
 * - The AI reference: the pages `meridian doctor --ai` pastes into its prompt. The site serves it
 *   at `/ai/reference.md`; `make ai-docs` and the PyPI job write it into the package:
 *
 *       node website/scripts/ai-docs.mjs reference src/meridian/data/ai-reference.md
 *
 * The site's Markdown twins reuse `markdownBody()`. Output depends only on the docs (no dates),
 * and the module needs nothing beyond Node, so the Makefile can run it without `pnpm install`.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { APPS_MARKER, appListMarkdown } from '../src/lib/apps-list.mjs';
import { DOCS_SLUGS } from '../src/lib/docs-sidebar.mjs';

export const SITE = 'https://getmeridian.org';

/** Pages in the AI reference, most useful for troubleshooting first. */
export const AI_REFERENCE_PAGES = ['cli-reference', 'architecture', 'troubleshooting', 'deploy', 'relay', 'recovery'];

const FRONT_MATTER = /^---\n([\s\S]*?)\n---\n/;

/** Title and description from a page's front matter (plain one-line scalars). */
function frontMatter(raw) {
  const fields = {};
  for (const line of (raw.match(FRONT_MATTER)?.[1] ?? '').split('\n')) {
    const match = line.match(/^(\w+):\s*(.*)$/);
    if (match) fields[match[1]] = match[2].replace(/^(['"])(.*)\1$/, '$2');
  }
  return fields;
}

/**
 * A page's body without front matter. Links to other docs pages point at their Markdown twins;
 * `absolute` also prefixes every site-relative link with the site, for text read off-site.
 */
export function markdownBody(raw, { absolute = false } = {}) {
  const body = raw
    .replace(FRONT_MATTER, '')
    .replace(APPS_MARKER, () => appListMarkdown())
    .replace(/\]\(\/(en|ru|fa|zh)\/([a-z0-9-]+)\/?(#[^)]*)?\)/g, (_m, locale, slug, hash = '') => `](/${locale}/${slug}.md${hash})`)
    .trim();
  return absolute ? body.replace(/\]\(\/(?!\/)/g, `](${SITE}/`) : body;
}

/**
 * One English page: its title, description, and body with absolute links. Paths are relative to
 * the website folder, where Astro runs and the CLI changes to (bundling moves import.meta.url).
 */
export function readDoc(slug) {
  const raw = fs.readFileSync(path.resolve('src/content/docs/en', `${slug}.md`), 'utf8');
  const { title = slug, description = '' } = frontMatter(raw);
  return { slug, title, description, body: markdownBody(raw, { absolute: true }) };
}

function bundle(heading, intro, slugs) {
  const docs = slugs.map(readDoc);
  return [
    `# ${heading}`,
    '',
    ...intro.map((line) => `> ${line}`),
    '',
    '## Contents',
    '',
    ...docs.map((doc, i) => `${i + 1}. [${doc.title}](${SITE}/en/${doc.slug}.md)`),
    '',
    ...docs.map((doc) => `---\n\n# ${doc.title}\n\n${doc.description ? `> ${doc.description}\n\n` : ''}${doc.body}\n`),
  ].join('\n');
}

/** Every English docs page in one file (`/llms-full.txt`). */
export function llmsFull() {
  return bundle(
    'Meridian documentation',
    [
      'Deploy a hardened self-hosted VPN server with one command, with a built-in web panel and a connection page for each person.',
      `Source: ${SITE}`,
    ],
    DOCS_SLUGS,
  );
}

/** The troubleshooting subset `meridian doctor --ai` and `meridian check --ai` include. */
export function aiReference() {
  return bundle(
    'Meridian reference for AI assistants',
    ['Selected pages of the Meridian documentation.', `All pages: ${SITE}/llms-full.txt`],
    AI_REFERENCE_PAGES,
  );
}

const BUNDLES = { reference: aiReference, 'llms-full': llmsFull };

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const [name, out] = process.argv.slice(2);
  if (!BUNDLES[name] || !out) {
    console.error(`usage: node ai-docs.mjs {${Object.keys(BUNDLES).join('|')}} OUT`);
    process.exit(2);
  }
  const target = path.resolve(out);
  process.chdir(fileURLToPath(new URL('..', import.meta.url)));
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, BUNDLES[name]());
  console.log(`${out}: ${name} bundle`);
}
