/**
 * Mermaid diagrams render at authoring time, not in the browser. Each ```mermaid block in
 * src/content stays in the Markdown as its source (agents read it in the Markdown twins), and a
 * checked-in SVG named after the source's hash replaces it in the HTML. The SVGs take their
 * colors from the page's CSS variables, so one file fits light and dark themes.
 *
 *   node scripts/diagrams.mjs          # check every block has a current SVG (part of the build)
 *   node scripts/diagrams.mjs render   # render missing SVGs (needs uv and Chromium; see figures/)
 *
 * Dependency-free so the check runs before `astro build`.
 */
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

/** Bump when figures/render_diagrams.py changes its output, so every SVG renders again. */
export const RENDER_VERSION = 1;

// Paths are relative to the website folder, where Astro and the scripts run.
export const CONTENT_DIR = 'src/content';
export const DIAGRAM_DIR = 'src/assets/diagrams';

const FENCE = /^```mermaid[^\n]*\n([\s\S]*?)^```[ \t]*$/gm;

function normalize(source) {
  return source.replace(/\r\n/g, '\n').trim();
}

/** The SVG's file stem: a hash of the diagram source and the renderer version. */
export function diagramId(source) {
  const hash = crypto.createHash('sha256').update(`${RENDER_VERSION}\n${normalize(source)}\n`).digest('hex');
  return `mmd-${hash.slice(0, 12)}`;
}

export function diagramFile(id, root = '.') {
  return path.join(root, DIAGRAM_DIR, `${id}.svg`);
}

function markdownFiles(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return markdownFiles(full);
    return entry.name.endsWith('.md') ? [full] : [];
  });
}

/** Every Mermaid block under src/content, with where it was found. */
export function collectDiagrams(root = '.') {
  return markdownFiles(path.join(root, CONTENT_DIR)).flatMap((file) => {
    const text = fs.readFileSync(file, 'utf8');
    return [...text.matchAll(FENCE)].map((match) => ({
      file: path.relative(root, file),
      line: text.slice(0, match.index).split('\n').length,
      source: normalize(match[1]),
      id: diagramId(match[1]),
    }));
  });
}

/** Missing and orphaned SVGs; both empty when the checked-in diagrams match the sources. */
export function diagramDrift(root = '.') {
  const diagrams = collectDiagrams(root);
  const wanted = new Set(diagrams.map((diagram) => diagram.id));
  const dir = path.join(root, DIAGRAM_DIR);
  const present = fs.existsSync(dir) ? fs.readdirSync(dir).filter((name) => name.endsWith('.svg')) : [];
  return {
    diagrams,
    missing: diagrams.filter((diagram) => !fs.existsSync(diagramFile(diagram.id, root))),
    orphans: present.filter((name) => !wanted.has(name.slice(0, -4))),
  };
}

/**
 * Remark plugin: swaps each Mermaid block for its rendered SVG. The figure is always
 * left-to-right — the labels are code-like names, and an RTL page would reorder their punctuation.
 */
export function remarkDiagrams() {
  const visit = (node, file) => {
    node.children?.forEach((child, index) => {
      if (child.type === 'code' && child.lang === 'mermaid') {
        const svgPath = diagramFile(diagramId(child.value));
        if (!fs.existsSync(svgPath)) {
          throw new Error(
            `${file.path ?? 'Markdown'}: no rendered diagram ${svgPath} for a Mermaid block; ` +
              'run `node scripts/diagrams.mjs render` in website/',
          );
        }
        node.children[index] = {
          type: 'html',
          value: `<figure class="diagram" dir="ltr">${fs.readFileSync(svgPath, 'utf8').trim()}</figure>`,
        };
      } else {
        visit(child, file);
      }
    });
  };
  return (tree, file) => visit(tree, file);
}
