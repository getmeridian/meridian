import type { APIRoute } from 'astro';
import { getCollection } from 'astro:content';

export const GET: APIRoute = async () => {
  const docs = await getCollection('docs');
  const enDocs = docs.filter(doc => doc.id.startsWith('en/'));
  const sorted = enDocs.sort((a, b) => (a.data.order ?? 99) - (b.data.order ?? 99));

  const lines = [
    '# Meridian',
    '',
    '> Censorship-resistant proxy deployment CLI. One command deploys a fully configured, undetectable VLESS+Reality proxy server.',
    '',
    '## Docs',
    '',
    ...sorted.map(doc => `- [${doc.data.title}](https://getmeridian.org/md/${doc.id}/): ${doc.data.description || ''}`),
    '',
    '## Links',
    '',
    '- [Website](https://getmeridian.org)',
    '- [GitHub](https://github.com/getmeridian/meridian)',
    '- [Full docs as single file](https://getmeridian.org/llms-full.txt)',
    '',
  ];

  return new Response(lines.join('\n'), {
    headers: { 'Content-Type': 'text/plain; charset=utf-8' },
  });
};
