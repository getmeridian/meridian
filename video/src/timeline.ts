import narration from "../narration/en.json";
import audio from "../public/audio/en/manifest.json";
import { FPS } from "./theme";

type Manifest = Record<string, { hash: string; seconds: number }>;
const manifest = audio as Manifest;

export const VOICE_DELAY = 0.35; // seconds of picture before each scene's narration starts
const TAIL = 0.9; // seconds after the narration ends
const CHARS_PER_SECOND = 13; // a placeholder pace until a scene has audio

export type Scene = {
  id: string;
  text: string;
  frames: number;
  audio: string | null; // public path, when generated for the current text
};

export const scenes: Scene[] = narration.scenes.map((scene) => {
  const entry = manifest[scene.id];
  const seconds = entry ? entry.seconds : scene.text.length / CHARS_PER_SECOND;
  return {
    id: scene.id,
    text: scene.text,
    frames: Math.ceil((VOICE_DELAY + seconds + TAIL) * FPS),
    audio: entry ? `audio/en/${scene.id}.mp3` : null,
  };
});

export const totalFrames = scenes.reduce((sum, scene) => sum + scene.frames, 0);
