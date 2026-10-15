// Upload files for the vertical short (pnpm render:short, or render:short:ru, first).
//
//   node scripts/export-short.mjs [en|ru]
//
// out/tiktok.<lang>.mp4        the render with its sound normalized to -14 LUFS, the level TikTok,
//                              Shorts, and Reels play at (a quieter upload just sounds weak)
// out/tiktok-cover.<lang>.jpg  the hook card, for the cover picker
import { execFileSync, spawnSync } from "node:child_process";
import { existsSync } from "node:fs";

const lang = process.argv[2] ?? "en";
const SOURCE = `out/meridian-short.${lang}.mp4`;
if (!existsSync(SOURCE)) throw new Error(`Render ${SOURCE} first`);
const TARGET = "I=-14:TP=-1.5:LRA=11";

// Two-pass loudnorm: measure first, then apply one linear gain so the mix is not compressed.
const probe = spawnSync(
  "ffmpeg",
  ["-hide_banner", "-nostats", "-i", SOURCE, "-af", `loudnorm=${TARGET}:print_format=json`, "-f", "null", "-"],
  { encoding: "utf8" },
);
const json = probe.stderr.slice(probe.stderr.lastIndexOf("{"));
const stats = JSON.parse(json.slice(0, json.indexOf("}") + 1)); // ffmpeg prints a summary after it
const gain = [
  `loudnorm=${TARGET}`,
  `measured_I=${stats.input_i}`,
  `measured_TP=${stats.input_tp}`,
  `measured_LRA=${stats.input_lra}`,
  `measured_thresh=${stats.input_thresh}`,
  `offset=${stats.target_offset}`,
  "linear=true",
].join(":");

const run = (args) =>
  execFileSync("ffmpeg", ["-y", "-loglevel", "error", ...args], { stdio: ["ignore", "ignore", "inherit"] });
const audio = ["-af", gain, "-ar", "48000", "-c:a", "aac", "-b:a", "192k"];
run(["-i", SOURCE, "-c:v", "copy", ...audio, "-movflags", "+faststart", `out/tiktok.${lang}.mp4`]);
run(["-ss", "1.2", "-i", SOURCE, "-frames:v", "1", "-q:v", "3", `out/tiktok-cover.${lang}.jpg`]);
console.log(`out/tiktok.${lang}.mp4 (${stats.input_i} → -14 LUFS), out/tiktok-cover.${lang}.jpg`);
