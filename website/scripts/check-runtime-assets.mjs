import { readdir, readFile, stat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const websiteRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const distRoot = path.join(websiteRoot, "dist");
const failures = [];

async function walk(dir) {
  const entries = await readdir(dir, { withFileTypes: true });
  for (const entry of entries) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      await walk(full);
    } else if (entry.isFile() && /\.(html|css)$/i.test(entry.name)) {
      await scanFile(full);
    }
  }
}

async function scanFile(file) {
  const text = await readFile(file, "utf8");
  const rel = path.relative(distRoot, file);
  const checks = [
    /<(script|iframe|img|source|video|audio|embed|object)\b[^>]*(?:src|data)=["']https?:\/\//gi,
    /<link\b(?=[^>]*\brel=["'](?:stylesheet|preload|modulepreload|icon|apple-touch-icon)["'])(?=[^>]*\bhref=["']https?:\/\/)[^>]*>/gi,
    /<link\b(?=[^>]*\bhref=["']https?:\/\/)(?=[^>]*\brel=["'](?:stylesheet|preload|modulepreload|icon|apple-touch-icon)["'])[^>]*>/gi,
    /@import\s+["']https?:\/\//gi,
    /url\(\s*["']?https?:\/\//gi,
  ];
  for (const pattern of checks) {
    for (const match of text.matchAll(pattern)) {
      failures.push(`${rel}: ${match[0].slice(0, 180)}`);
    }
  }
}

try {
  const info = await stat(distRoot);
  if (!info.isDirectory()) {
    throw new Error(`${distRoot} is not a directory`);
  }
  await walk(distRoot);
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
}

if (failures.length > 0) {
  console.error("External runtime assets are not allowed:");
  for (const failure of failures) {
    console.error(`- ${failure}`);
  }
  process.exit(1);
}

console.log("OK: no external runtime assets in dist");
