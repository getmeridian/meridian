// Render one still from the middle of every scene into out/stills/ for a quick visual review.
//   node scripts/stills.mjs [--at 0.6] [scene ...]
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";

const narration = JSON.parse(readFileSync("narration/en.json", "utf8"));
const manifestPath = "public/audio/en/manifest.json";
const manifest = existsSync(manifestPath) ? JSON.parse(readFileSync(manifestPath, "utf8")) : {};
const args = process.argv.slice(2);
const atIndex = args.indexOf("--at");
const at = atIndex >= 0 ? Number(args.splice(atIndex, 2)[1]) : 0.6;
const wanted = args;
let start = 0;
for (const scene of narration.scenes) {
  const seconds = manifest[scene.id]?.seconds ?? scene.text.length / 13; // mirrors src/timeline.ts
  const frames = Math.ceil((0.35 + seconds + 0.9) * 30);
  if (!wanted.length || wanted.includes(scene.id)) {
    const frame = start + Math.floor(frames * at);
    execFileSync("npx", ["remotion", "still", "src/index.ts", "Setup", `out/stills/${scene.id}-${at}.png`, `--frame=${frame}`, "--log=error"], { stdio: "inherit" });
    console.log(`${scene.id} @ ${frame}`);
  }
  start += frames;
}
