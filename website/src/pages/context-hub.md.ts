import type { APIRoute } from 'astro';
import { getCollection } from 'astro:content';
import fs from 'node:fs';
import path from 'node:path';

export const GET: APIRoute = async () => {
  const docs = await getCollection('docs');
  const enDocs = docs.filter(doc => doc.id.startsWith('en/'));
  const sorted = enDocs.sort((a, b) => (a.data.order ?? 99) - (b.data.order ?? 99));

  // Read version from VERSION file
  let version = '0.0.0';
  try {
    version = fs.readFileSync(path.resolve('../../VERSION'), 'utf-8').trim();
  } catch {
    // fallback
  }

  const today = new Date().toISOString().split('T')[0];

  const sections: string[] = [
    '---',
    'name: meridian/cli',
    'description: Meridian CLI — deploy censorship-resistant VLESS+Reality proxy servers on any VPS with one command',
    'metadata:',
    '  languages: [python]',
    `  versions: ["${version}"]`,
    '  revision: 1',
    `  updated-on: ${today}`,
    '  source: official',
    '  tags: [cli, vpn, proxy, censorship, vless, reality, xray, deployment]',
    '---',
    '',
    '# Meridian',
    '',
    '> Censorship-resistant proxy deployment CLI. One command deploys a fully configured, undetectable VLESS+Reality proxy server.',
    '> Source: https://getmeridian.org',
    '> GitHub: https://github.com/getmeridian/meridian',
    '> llms.txt: https://getmeridian.org/llms.txt',
    '',
  ];

  for (const doc of sorted) {
    const filePath = path.resolve(`src/content/docs/${doc.id}.md`);
    try {
      const raw = fs.readFileSync(filePath, 'utf-8');
      let content = raw.replace(/^---[\s\S]*?---\n/, '');
      content = content.replace(/\(\/docs\//g, '(/md/');
      sections.push(`## ${doc.data.title}\n\n${content.trim()}\n`);
    } catch {
      sections.push(`## ${doc.data.title}\n\n(Content not available)\n`);
    }
  }

  return new Response(sections.join('\n'), {
    headers: { 'Content-Type': 'text/markdown; charset=utf-8' },
  });
};
