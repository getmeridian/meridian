/**
 * The docs sidebar in reading order. Each slug names `src/content/docs/<locale>/<slug>.md`,
 * and every locale uses the same list. It also orders `/llms.txt` and the AI bundles, so a
 * new page is added here once.
 */
export const DOCS_SIDEBAR = [
  {
    label: 'Guides',
    translations: { ru: 'Руководства', fa: 'راهنماها', 'zh-CN': '指南' },
    items: [
      'getting-started',
      'installation',
      'wsl',
      'hosting',
      'deploy',
      'domain-mode',
      'client-management',
      'relay',
      'split-routing',
      'routers',
      'recovery',
      'troubleshooting',
    ],
  },
  {
    label: 'Reference',
    translations: { ru: 'Справочник', fa: 'مرجع', 'zh-CN': '参考' },
    items: ['architecture', 'cli-reference', 'agents', 'security', 'contributing'],
  },
];

/** Every docs slug in sidebar order. */
export const DOCS_SLUGS = DOCS_SIDEBAR.flatMap((section) => section.items);
