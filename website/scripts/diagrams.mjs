/**
 * Check or render the Mermaid diagrams in src/content (see src/lib/diagrams.mjs).
 *
 *   node scripts/diagrams.mjs               # fail when an SVG is missing or orphaned
 *   node scripts/diagrams.mjs render        # render missing SVGs, delete orphans
 *   node scripts/diagrams.mjs render --all  # render every SVG again
 *
 * Rendering runs figures/render_diagrams.py through uv; extra arguments after `--` go to it
 * (for example `-- --channel chrome` to use an installed Chrome).
 */
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { DIAGRAM_DIR, diagramDrift } from '../src/lib/diagrams.mjs';

process.chdir(fileURLToPath(new URL('..', import.meta.url)));
const [mode = 'check', ...rest] = process.argv.slice(2);
const passthrough = rest.includes('--') ? rest.slice(rest.indexOf('--') + 1) : [];
const { diagrams, missing, orphans } = diagramDrift();

if (mode === 'check') {
  for (const diagram of missing) console.error(`${diagram.file}:${diagram.line}: no rendered SVG (${diagram.id}.svg)`);
  for (const name of orphans) console.error(`${DIAGRAM_DIR}/${name}: no Mermaid block uses it`);
  if (missing.length || orphans.length) {
    console.error('Diagrams are out of date; run `node scripts/diagrams.mjs render` in website/.');
    process.exit(1);
  }
  console.log(`OK: ${diagrams.length} Mermaid blocks have current SVGs`);
} else if (mode === 'render') {
  const todo = rest.includes('--all') ? diagrams : missing;
  const unique = [...new Map(todo.map((diagram) => [diagram.id, { id: diagram.id, source: diagram.source }])).values()];
  if (unique.length) {
    fs.mkdirSync(DIAGRAM_DIR, { recursive: true });
    const result = spawnSync('uv', ['run', 'figures/render_diagrams.py', '--out', DIAGRAM_DIR, ...passthrough], {
      input: JSON.stringify(unique),
      stdio: ['pipe', 'inherit', 'inherit'],
    });
    if (result.error || result.status !== 0) process.exit(result.status || 1);
  }
  for (const name of orphans) {
    fs.rmSync(path.join(DIAGRAM_DIR, name));
    console.log(`removed ${DIAGRAM_DIR}/${name}`);
  }
  console.log(`${unique.length} rendered, ${orphans.length} removed`);
} else {
  console.error('usage: node scripts/diagrams.mjs [check | render [--all] [-- renderer args]]');
  process.exit(2);
}
