/**
 * The recommended client apps from `src/data/apps.json` (the one app catalog), for docs pages.
 * A line holding only `<!-- apps -->` becomes a list of them: the remark plugin builds it for
 * the HTML page, and the Markdown twins and bundles call `appListMarkdown()`. Names and platform
 * names are not translated, so one list serves every locale.
 */
import fs from 'node:fs';
import path from 'node:path';

export const APPS_MARKER = '<!-- apps -->';

/** Apps the connection page features on at least one platform, in recommendation order. */
function recommendedApps() {
  const apps = JSON.parse(fs.readFileSync(path.resolve('src/data/apps.json'), 'utf8'));
  return apps.filter((app) => (app.recommended ?? []).length > 0);
}

export function appListMarkdown() {
  return recommendedApps()
    .map((app) => `- [${app.name}](${app.url}) — ${app.platforms.join(', ')}`)
    .join('\n');
}

/** Remark plugin: expands the marker into the same list as an mdast node. */
export function remarkAppList() {
  const item = (app) => ({
    type: 'listItem',
    spread: false,
    children: [
      {
        type: 'paragraph',
        children: [
          { type: 'link', url: app.url, children: [{ type: 'text', value: app.name }] },
          { type: 'text', value: ` — ${app.platforms.join(', ')}` },
        ],
      },
    ],
  });
  const visit = (node) => {
    node.children?.forEach((child, index) => {
      if (child.type === 'html' && child.value.trim() === APPS_MARKER) {
        node.children[index] = { type: 'list', ordered: false, spread: false, children: recommendedApps().map(item) };
      } else {
        visit(child);
      }
    });
  };
  return visit;
}
