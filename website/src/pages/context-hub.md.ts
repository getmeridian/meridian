import type { APIRoute } from 'astro';
import { docBody, docsInOrder, markdownResponse } from '../lib/docs-markdown';
import fs from 'node:fs';
import path from 'node:path';

export const GET: APIRoute = async () => {
  const sorted = await docsInOrder('en');

  // Read version from VERSION file
  let version = '0.0.0';
  try {
    version = fs.readFileSync(path.resolve('../VERSION'), 'utf-8').trim();
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
    '> Censorship-resistant proxy deployment CLI. One command deploys a hardened VLESS+Reality proxy server, with XHTTP and Hysteria2 fallbacks, and a link to share.',
    '> Source: https://getmeridian.org',
    '> GitHub: https://github.com/getmeridian/meridian',
    '> llms.txt: https://getmeridian.org/llms.txt',
    '',
  ];

  for (const doc of sorted) {
    sections.push(`## ${doc.data.title}\n\n${docBody(doc)}\n`);
  }

  return markdownResponse(sections.join('\n'));
};
