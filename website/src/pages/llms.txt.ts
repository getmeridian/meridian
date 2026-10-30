import type { APIRoute } from 'astro';
import { docsInOrder, markdownResponse, SITE } from '../lib/docs-markdown';

export const GET: APIRoute = async () => {
  const docs = await docsInOrder('en');
  const lines = [
    '# Meridian',
    '',
    '> Deploy a hardened self-hosted VPN server with one command. Meridian sets up VLESS+Reality, XHTTP, and Hysteria2 behind a hardened firewall, verifies real traffic, and gives you a built-in web panel and a connection page for each person.',
    '',
    'Driving the CLI as an agent? Start with the agent guide, also printed by `meridian help agents`.',
    '',
    '## Agents',
    '',
    `- [Agent guide](${SITE}/agents.md): how Meridian works and how to drive it safely`,
    '',
    '## Docs',
    '',
    ...docs.map((doc) => `- [${doc.data.title}](${SITE}/${doc.id}.md): ${doc.data.description ?? ''}`),
    '',
    '## Optional',
    '',
    `- [All docs in one file](${SITE}/llms-full.txt)`,
    '- [Source code](https://github.com/getmeridian/meridian)',
    '',
  ];
  return markdownResponse(lines.join('\n'));
};
