import en from "../narration/en.json";
import ru from "../narration/ru.json";
import enAudio from "../public/audio/en/manifest.json";
import ruAudio from "../public/audio/ru/manifest.json";
import shortEn from "../narration/short-en.json";
import shortRu from "../narration/short-ru.json";
import shortEnAudio from "../public/audio/short-en/manifest.json";
import shortRuAudio from "../public/audio/short-ru/manifest.json";
import type { Lang } from "./copy";
import { SHORT_PACE, sceneTimings } from "./timing.mjs";

export { captionCues, chunkCues, FPS, VOICE_DELAY } from "./timing.mjs";

const NARRATION = { en: [en, enAudio], ru: [ru, ruAudio] } as const;

export const scenesFor = (lang: Lang) => sceneTimings(NARRATION[lang][0], NARRATION[lang][1], lang);
export const totalFrames = (lang: Lang) => scenesFor(lang).reduce((sum, scene) => sum + scene.frames, 0);

const SHORT = { en: [shortEn, shortEnAudio], ru: [shortRu, shortRuAudio] } as const;

/** The vertical cut's scenes: narration/short-<lang>.json, voiced into public/audio/short-<lang>/. */
export const shortScenesFor = (lang: Lang) => sceneTimings(SHORT[lang][0], SHORT[lang][1], `short-${lang}`, SHORT_PACE);
export const shortFrames = (lang: Lang) => shortScenesFor(lang).reduce((sum, scene) => sum + scene.frames, 0);
