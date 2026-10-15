// Scene and caption timing, shared by the composition (src/timeline.ts) and the Node
// scripts (stills, subtitles, chapters), so the video and its exports never disagree.

export const FPS = 30;
export const VOICE_DELAY = 0.35; // seconds of picture before each scene's narration starts
const TAIL = 0.9; // seconds after the narration ends
const CHARS_PER_SECOND = 13; // a placeholder pace until a scene has audio

/**
 * @param {{ scenes: { id: string, text: string }[] }} narration
 * @param {Record<string, { hash: string, seconds: number }>} manifest
 * @param {string} lang
 */
export function sceneTimings(narration, manifest, lang = "en") {
  let start = 0;
  return narration.scenes.map((scene) => {
    const entry = manifest[scene.id];
    const speech = entry ? entry.seconds : scene.text.length / CHARS_PER_SECOND;
    const frames = Math.ceil((VOICE_DELAY + speech + TAIL) * FPS);
    const timing = {
      id: scene.id,
      text: scene.text,
      start, // frame where the scene begins
      frames,
      speech, // seconds of narration
      audio: entry ? `audio/${lang}/${scene.id}.mp3` : null,
    };
    start += frames;
    return timing;
  });
}

/**
 * The scene's sentences, each timed by its share of the narration, in seconds from the scene start.
 * @param {string} text
 * @param {number} speech
 */
export function captionCues(text, speech) {
  const sentences = text.match(/[^.!?]+[.!?]+(\s|$)/g)?.map((sentence) => sentence.trim()) ?? [text];
  const total = sentences.reduce((sum, sentence) => sum + sentence.length, 0);
  let at = VOICE_DELAY;
  return sentences.map((sentence) => {
    const length = (sentence.length / total) * speech;
    const cue = { start: at, end: at + length, text: sentence };
    at += length;
    return cue;
  });
}
