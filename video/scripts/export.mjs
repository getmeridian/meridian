// Publishing files from a finished render (pnpm render, or pnpm render:ru, first).
//
//   node scripts/export.mjs [en|ru]
//
// out/meridian-setup.<lang>.srt   subtitles (the same cues the video burns in)
// out/chapters.<lang>.txt         YouTube chapters
// out/thumbnail.<lang>.jpg        README and YouTube thumbnail (the Thumbnail still)
// ../website/public/video/        the 720p copy, poster, and thumbnail the website serves
import { execFileSync } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { COPY } from "../src/copy.ts";
import { captionCues, FPS, sceneTimings } from "../src/timing.mjs";

const lang = process.argv[2] ?? "en";
const suffix = lang === "en" ? "" : `.${lang}`; // English keeps the plain names the site and README link
const composition = (name) => (lang === "en" ? name : name + lang[0].toUpperCase() + lang.slice(1));
const SOURCE = `out/meridian-setup${suffix}.mp4`;
const SITE = "../website/public/video";

if (!existsSync(SOURCE)) throw new Error(`Render ${SOURCE} first`);
const narration = JSON.parse(readFileSync(`narration/${lang}.json`, "utf8"));
const manifest = JSON.parse(readFileSync(`public/audio/${lang}/manifest.json`, "utf8"));
const scenes = sceneTimings(narration, manifest, lang);
const chapters = COPY[lang].chapters;

const stamp = (seconds, separator) => {
  const ms = Math.round(seconds * 1000);
  const pad = (value, size = 2) => String(value).padStart(size, "0");
  return `${pad(Math.floor(ms / 3600000))}:${pad(Math.floor(ms / 60000) % 60)}:${pad(Math.floor(ms / 1000) % 60)}${separator}${pad(ms % 1000, 3)}`;
};
const cues = scenes.flatMap((scene) =>
  captionCues(scene.text, scene.speech).map((cue) => ({ ...cue, offset: scene.start / FPS })),
);
writeFileSync(
  `out/meridian-setup.${lang}.srt`,
  cues
    .map(
      (cue, index) =>
        `${index + 1}\n${stamp(cue.offset + cue.start, ",")} --> ${stamp(cue.offset + cue.end, ",")}\n${cue.text}\n`,
    )
    .join("\n"),
);

const chapter = (seconds) => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
writeFileSync(
  `out/chapters.${lang}.txt`,
  scenes
    .filter((scene) => chapters[scene.id])
    .map((scene) => `${chapter(scene.start / FPS)} ${chapters[scene.id]}`)
    .join("\n") + "\n",
);

const run = (command, args) => execFileSync(command, args, { stdio: ["ignore", "ignore", "inherit"] });
const thumbnail = `out/thumbnail.${lang}.jpg`;
run("npx", ["remotion", "still", "src/index.ts", composition("Thumbnail"), thumbnail, "--image-format=jpeg", "--log=error"]);
mkdirSync(SITE, { recursive: true });
// 720p H.264 with the index up front, so browsers start playing before the download ends.
const encode = "-vf scale=1280:720:flags=lanczos -c:v libx264 -preset slow -crf 27 -pix_fmt yuv420p -c:a aac -b:a 96k";
run("ffmpeg", ["-y", "-loglevel", "error", "-i", SOURCE, ...encode.split(" "), "-movflags", "+faststart", `${SITE}/meridian-setup${suffix}.mp4`]);
// The poster is the title card once it has settled.
const poster = ["-frames:v", "1", "-vf", "scale=1280:720", "-q:v", "4", `${SITE}/meridian-setup${suffix}-poster.jpg`];
run("ffmpeg", ["-y", "-loglevel", "error", "-ss", "2.5", "-i", SOURCE, ...poster]);
copyFileSync(thumbnail, `${SITE}/meridian-setup${suffix}-thumbnail.jpg`);

console.log(readFileSync(`out/chapters.${lang}.txt`, "utf8"));
console.log(`${cues.length} subtitle cues; site files in ${SITE}`);
