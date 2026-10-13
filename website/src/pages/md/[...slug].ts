import type { APIRoute, GetStaticPaths } from 'astro';
import { getCollection } from 'astro:content';
import { docMarkdown, markdownResponse, type DocEntry } from '../../lib/docs-markdown';

// /md/en/getting-started/ — the same Markdown as /en/getting-started.md, at its original address.
export const getStaticPaths: GetStaticPaths = async () =>
  (await getCollection('docs')).map((entry) => ({ params: { slug: entry.id }, props: { entry } }));

export const GET: APIRoute = ({ props }) => markdownResponse(docMarkdown(props.entry as DocEntry));
