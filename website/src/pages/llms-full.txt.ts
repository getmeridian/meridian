import type { APIRoute } from 'astro';
import { docBody, docsInOrder, markdownResponse, SITE } from '../lib/docs-markdown';

export const GET: APIRoute = async () => {
  const docs = await docsInOrder('en');
  const sections = [
    '# Meridian documentation',
    '',
    '> Deploy censorship-resistant proxy servers with one command, hardened by default.',
    `> Source: ${SITE}`,
    '',
    '## Contents',
    '',
    ...docs.map((doc, i) => `${i + 1}. ${doc.data.title}`),
    '',
    ...docs.map((doc) => `---\n\n# ${doc.data.title}\n\n${docBody(doc)}\n`),
  ];
  return markdownResponse(sections.join('\n'));
};
