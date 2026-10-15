import en from "../narration/en.json";
import ru from "../narration/ru.json";
import enAudio from "../public/audio/en/manifest.json";
import ruAudio from "../public/audio/ru/manifest.json";
import type { Lang } from "./copy";
import { sceneTimings } from "./timing.mjs";

export { captionCues, FPS, VOICE_DELAY } from "./timing.mjs";

const NARRATION = { en: [en, enAudio], ru: [ru, ruAudio] } as const;

export const scenesFor = (lang: Lang) => sceneTimings(NARRATION[lang][0], NARRATION[lang][1], lang);
export const totalFrames = (lang: Lang) => scenesFor(lang).reduce((sum, scene) => sum + scene.frames, 0);
