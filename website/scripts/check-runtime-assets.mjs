/**
 * Fails when the built site would make the browser request anything from another origin.
 *
 *   node scripts/check-runtime-assets.mjs [DIST]   # default: website/dist
 *
 * HTML and CSS are checked for external tags and url()s; JavaScript (bundles and inline
 * scripts) for loads whose address is a literal absolute URL: import, fetch, workers, sockets,
 * beacons, and src assignments. URLs that are merely text (credits, error messages, `new URL`
 * bases) are not loads and pass, and neither are loopback addresses.
 */
import { readdir, readFile, stat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

// A literal absolute URL; loopback (Studio's local Engine) is not another origin on the network.
const ABSOLUTE = String.raw`(["'\x60])(?:https?:|wss?:)?\/\/(?!(?:localhost|127\.0\.0\.1|\[::1\])[:/"'\x60])`;

const MARKUP_CHECKS = [
  /<(script|iframe|img|source|video|audio|embed|object)\b[^>]*(?:src|data)=["']https?:\/\//gi,
  /<link\b(?=[^>]*\brel=["'](?:stylesheet|preload|modulepreload|icon|apple-touch-icon)["'])(?=[^>]*\bhref=["']https?:\/\/)[^>]*>/gi,
  /<link\b(?=[^>]*\bhref=["']https?:\/\/)(?=[^>]*\brel=["'](?:stylesheet|preload|modulepreload|icon|apple-touch-icon)["'])[^>]*>/gi,
];
const STYLE_CHECKS = [/@import\s+["']https?:\/\//gi, /url\(\s*["']?https?:\/\//gi];
const SCRIPT_CHECKS = [
  // import("https://…"), fetch(…), importScripts(…), navigator.sendBeacon(…), new Worker(…), …
  new RegExp(
    String.raw`(?<![\w$])(?:import|fetch|importScripts|sendBeacon|new\s+(?:Shared)?Worker|new\s+EventSource|new\s+WebSocket)\s*\(\s*` +
      ABSOLUTE,
    "g",
  ),
  // import x from "https://…", import "https://…", export * from "https://…"
  new RegExp(String.raw`(?<![\w$.])(?:import|export)\s*(?:[\w$*{}\s,]+?\s*from\s*)?` + ABSOLUTE, "g"),
  // el.src = "https://…", el.setAttribute("src", "https://…")
  new RegExp(String.raw`\.src\s*=\s*` + ABSOLUTE, "g"),
  new RegExp(String.raw`\.setAttribute\(\s*["'\x60](?:src|href)["'\x60]\s*,\s*` + ABSOLUTE, "g"),
];

/** External loads in one file's text, by file kind ("html", "css", or "js"). */
export function findExternalLoads(text, kind) {
  const found = new Map(); // offset -> match; one tag may match several patterns
  const scan = (source, checks, offset = 0) => {
    for (const pattern of checks) {
      for (const match of source.matchAll(pattern)) found.set(offset + match.index, match[0].slice(0, 180));
    }
  };
  if (kind === "html") {
    scan(text, MARKUP_CHECKS);
    scan(text, STYLE_CHECKS);
    for (const script of text.matchAll(/<script\b(?![^>]*\bsrc=)(?![^>]*\btype=["']application\/(?:ld\+)?json["'])[^>]*>([\s\S]*?)<\/script>/gi)) {
      scan(script[1], SCRIPT_CHECKS, script.index + script[0].indexOf('>') + 1);
    }
  } else if (kind === "css") {
    scan(text, STYLE_CHECKS);
  } else if (kind === "js") {
    scan(text, SCRIPT_CHECKS);
  }
  return [...found.values()];
}

const KINDS = { ".html": "html", ".css": "css", ".js": "js", ".mjs": "js" };

async function walk(dir, root, failures) {
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    const kind = KINDS[path.extname(entry.name).toLowerCase()];
    if (entry.isDirectory()) {
      await walk(full, root, failures);
    } else if (entry.isFile() && kind) {
      for (const load of findExternalLoads(await readFile(full, "utf8"), kind)) {
        failures.push(`${path.relative(root, full)}: ${load}`);
      }
    }
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const distRoot = path.resolve(process.argv[2] ?? fileURLToPath(new URL("../dist", import.meta.url)));
  const failures = [];
  try {
    if (!(await stat(distRoot)).isDirectory()) throw new Error(`${distRoot} is not a directory`);
    await walk(distRoot, distRoot, failures);
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    process.exit(1);
  }
  if (failures.length > 0) {
    console.error("External runtime assets are not allowed:");
    for (const failure of failures) console.error(`- ${failure}`);
    process.exit(1);
  }
  console.log(`OK: no external runtime assets in ${process.argv[2] ?? "dist"}`);
}
