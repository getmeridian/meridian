// Generate narration with ElevenLabs, one MP3 per scene, cached by text hash.
//
//   pnpm tts             # dry run: which scenes would be generated, and the character cost
//   pnpm tts --spend     # call the API for changed scenes only
//   pnpm tts --spend --only intro,outro
//   pnpm tts --lang ru   # narration/ru.json -> public/audio/ru/
//   pnpm tts --narration short-en --spend   # narration/short-en.json -> public/audio/short-en/
//
// Credits are scarce: a scene is regenerated only when its text, voice, model,
// or settings change. The key comes from ELEVENLABS_API_KEY or
// ~/.cache/meridian-video/elevenlabs.env and is never written anywhere else.
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const args = process.argv.slice(2);
const flag = (name) => args.includes(name);
const option = (name, fallback) => {
  const index = args.indexOf(name);
  return index >= 0 ? args[index + 1] : fallback;
};

const name = option("--narration", option("--lang", "en"));
const only = option("--only", "")?.split(",").filter(Boolean) ?? [];
const narration = JSON.parse(readFileSync(join(root, "narration", `${name}.json`), "utf8"));
const lang = narration.language ?? name;
if (narration.engine === "qwen3") {
  console.log(
    `narration/${name}.json is voiced locally: uv run --python 3.12 --with mlx-audio --with soundfile python scripts/tts_local.py ${name}`,
  );
  process.exit(0);
}
const outDir = join(root, "public", "audio", name);
const manifestPath = join(outDir, "manifest.json");
const manifest = existsSync(manifestPath) ? JSON.parse(readFileSync(manifestPath, "utf8")) : {};
const settings = narration.voiceSettings ?? null;
const seed = narration.seed ?? 7;

const fingerprint = (scene) =>
  createHash("sha256")
    .update(JSON.stringify([scene.text, narration.voice, narration.model, settings, seed]))
    .digest("hex")
    .slice(0, 12);

const scenes = narration.scenes.map((scene, index) => ({
  ...scene,
  hash: fingerprint(scene),
  previous: narration.scenes[index - 1]?.text,
  next: narration.scenes[index + 1]?.text,
}));
const stale = scenes.filter(
  (scene) =>
    (only.length === 0 || only.includes(scene.id)) &&
    (manifest[scene.id]?.hash !== scene.hash || !existsSync(join(outDir, `${scene.id}.mp3`))),
);
const characters = stale.reduce((sum, scene) => sum + scene.text.length, 0);
console.log(`${stale.length} of ${scenes.length} scenes to generate, ${characters} characters (${narration.model})`);
for (const scene of stale) console.log(`  ${scene.id.padEnd(12)} ${scene.text.length}`);
if (!flag("--spend") || stale.length === 0) {
  if (stale.length) console.log("Dry run; add --spend to call ElevenLabs.");
  process.exit(0);
}

const apiKey =
  process.env.ELEVENLABS_API_KEY ??
  readFileSync(join(homedir(), ".cache", "meridian-video", "elevenlabs.env"), "utf8").match(
    /ELEVENLABS_API_KEY=(\S+)/,
  )?.[1];
if (!apiKey) throw new Error("No ELEVENLABS_API_KEY in the environment or ~/.cache/meridian-video/elevenlabs.env");

mkdirSync(outDir, { recursive: true });
let spent = 0;
for (const scene of stale) {
  const response = await fetch(
    `https://api.elevenlabs.io/v1/text-to-speech/${narration.voice}?output_format=mp3_44100_128`,
    {
      method: "POST",
      headers: { "xi-api-key": apiKey, "content-type": "application/json", accept: "audio/mpeg" },
      body: JSON.stringify({
        text: scene.text,
        model_id: narration.model,
        language_code: lang,
        seed,
        ...(settings ? { voice_settings: settings } : {}),
        ...(scene.previous ? { previous_text: scene.previous } : {}),
        ...(scene.next ? { next_text: scene.next } : {}),
      }),
    },
  );
  if (!response.ok) throw new Error(`${scene.id}: HTTP ${response.status} ${await response.text()}`);
  const file = join(outDir, `${scene.id}.mp3`);
  writeFileSync(file, Buffer.from(await response.arrayBuffer()));
  const seconds = Number(
    execFileSync("ffprobe", ["-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", file], {
      encoding: "utf8",
    }).trim(),
  );
  spent += Number(response.headers.get("character-cost") ?? scene.text.length);
  manifest[scene.id] = { hash: scene.hash, seconds: Math.round(seconds * 1000) / 1000 };
  writeFileSync(manifestPath, JSON.stringify(manifest, null, 2) + "\n"); // keep progress if a later scene fails
  console.log(`  ✓ ${scene.id} ${manifest[scene.id].seconds}s`);
}
console.log(`Spent ${spent} characters.`);
