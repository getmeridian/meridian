/**
 * Markdown versions of the docs for agents and LLM tools. Every endpoint that serves
 * docs as Markdown (`/<locale>/<page>.md`, `/llms-full.txt`, `/context-hub.md`)
 * goes through here, so links and front matter are handled one way.
 */
import fs from 'node:fs';
import path from 'node:path';
import { getCollection, type CollectionEntry } from 'astro:content';

export type DocEntry = CollectionEntry<'docs'>;

export const SITE = 'https://getmeridian.org';

/** Docs pages of one locale in sidebar order. */
export async function docsInOrder(locale = 'en'): Promise<DocEntry[]> {
  const docs = await getCollection('docs', (doc) => doc.id.startsWith(`${locale}/`));
  return docs.sort((a, b) => (a.data.order ?? 99) - (b.data.order ?? 99));
}

/** The page body without front matter; links to other docs point at their Markdown twins. */
export function docBody(entry: DocEntry): string {
  const raw = fs.readFileSync(path.resolve(`src/content/docs/${entry.id}.md`), 'utf-8');
  return raw
    .replace(/^---[\s\S]*?---\n/, '')
    .replace(/\]\(\/(en|ru|fa|zh)\/([a-z0-9-]+)\/?(#[^)]*)?\)/g, (_m, locale, slug, hash = '') => `](/${locale}/${slug}.md${hash})`)
    .trim();
}

/** One page as a standalone Markdown document. */
export function docMarkdown(entry: DocEntry): string {
  const summary = entry.data.description ? `> ${entry.data.description}\n\n` : '';
  return `# ${entry.data.title}\n\n${summary}${docBody(entry)}\n`;
}

/** Static builds keep only the body; the host picks the content type from the file extension. */
export function markdownResponse(body: string): Response {
  return new Response(body);
}
