/**
 * Markdown versions of the docs for agents and LLM tools. The per-page twins
 * (`/<locale>/<page>.md`) and `/llms.txt` render here; the bundles (`/llms-full.txt`,
 * `/ai/reference.md`) come from `scripts/ai-docs.mjs`, which owns link and front matter handling.
 */
import fs from 'node:fs';
import path from 'node:path';
import { getCollection, type CollectionEntry } from 'astro:content';
import { markdownBody } from '../../scripts/ai-docs.mjs';
import { DOCS_SLUGS } from './docs-sidebar.mjs';

export type DocEntry = CollectionEntry<'docs'>;

export const SITE = 'https://getmeridian.org';

/** Docs pages of one locale in sidebar order. */
export async function docsInOrder(locale = 'en'): Promise<DocEntry[]> {
  const docs = await getCollection('docs', (doc) => doc.id.startsWith(`${locale}/`));
  const rank = (doc: DocEntry) => DOCS_SLUGS.indexOf(doc.id.slice(locale.length + 1));
  return docs.sort((a, b) => rank(a) - rank(b));
}

/** The page body without front matter; links to other docs point at their Markdown twins. */
export function docBody(entry: DocEntry): string {
  return markdownBody(fs.readFileSync(path.resolve(`src/content/docs/${entry.id}.md`), 'utf-8'));
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
