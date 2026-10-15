// Render one still per scene into out/stills/ for a quick visual review.
//   node scripts/stills.mjs [--lang ru] [--short] [--at 0.6] [scene ...]
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { SHORT_PACE, sceneTimings } from "../src/timing.mjs";

const args = process.argv.slice(2);
const option = (name, fallback) => {
  const index = args.indexOf(name);
  return index >= 0 ? args.splice(index, 2)[1] : fallback;
};
const short = args.includes("--short") && args.splice(args.indexOf("--short"), 1).length > 0;
const lang = option("--lang", "en");
const at = Number(option("--at", 0.6));
const base = short ? "Short" : "Setup";
const composition = lang === "en" ? base : base + lang[0].toUpperCase() + lang.slice(1);
const name = short ? `short-${lang}` : lang;
const narration = JSON.parse(readFileSync(`narration/${name}.json`, "utf8"));
const manifestPath = `public/audio/${name}/manifest.json`;
const manifest = existsSync(manifestPath) ? JSON.parse(readFileSync(manifestPath, "utf8")) : {};
for (const scene of sceneTimings(narration, manifest, name, ...(short ? [SHORT_PACE] : []))) {
  if (args.length && !args.includes(scene.id)) continue;
  const frame = scene.start + Math.floor(scene.frames * at);
  const out = `out/stills/${name}-${scene.id}-${at}.png`;
  execFileSync("npx", ["remotion", "still", "src/index.ts", composition, out, `--frame=${frame}`, "--log=error"], {
    stdio: "inherit",
  });
  console.log(`${scene.id} @ ${frame}`);
}
