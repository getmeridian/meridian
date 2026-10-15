// Scene and caption timing, shared by the composition (src/timeline.ts) and the Node
// scripts (stills, subtitles, chapters), so the video and its exports never disagree.

export const FPS = 30;
export const VOICE_DELAY = 0.35; // seconds of picture before each scene's narration starts
const TAIL = 0.9; // seconds after the narration ends
const CHARS_PER_SECOND = 13; // a placeholder pace until a scene has audio

// The vertical short cuts quicker: less air before and after each line.
export const SHORT_PACE = { delay: 0.15, tail: 0.35 };

/**
 * @param {{ scenes: { id: string, text: string }[] }} narration
 * @param {Record<string, { hash: string, seconds: number }>} manifest
 * @param {string} audioDir directory under public/audio/ (the language, or e.g. "short-en")
 * @param {{ delay: number, tail: number }} pace
 */
export function sceneTimings(narration, manifest, audioDir = "en", pace = { delay: VOICE_DELAY, tail: TAIL }) {
  let start = 0;
  return narration.scenes.map((scene) => {
    const entry = manifest[scene.id];
    const speech = entry ? entry.seconds : scene.text.length / CHARS_PER_SECOND;
    const frames = Math.ceil((pace.delay + speech + pace.tail) * FPS);
    const timing = {
      id: scene.id,
      text: scene.text,
      start, // frame where the scene begins
      frames,
      speech, // seconds of narration
      delay: pace.delay, // seconds before the narration starts
      audio: entry ? `audio/${audioDir}/${scene.id}.mp3` : null,
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

/**
 * Short on-screen phrases for the vertical cut: the text split at punctuation, then each phrase cut
 * into balanced pieces of about 20 characters (so no lone word dangles), each timed by its share
 * of the narration.
 * @param {string} text
 * @param {number} speech
 * @param {number} delay
 */
export function chunkCues(text, speech, delay = SHORT_PACE.delay) {
  // Punctuation ends a phrase only before a space, so "getmeridian.org" stays whole.
  const phrases = text
    .match(/.+?(?:[.!?:,;—]+(?=\s|$)|$)/g)
    ?.map((phrase) => phrase.trim())
    .filter(Boolean) ?? [text];
  const chunks = [];
  for (const phrase of phrases) {
    const words = phrase.split(/\s+/);
    const pieces = Math.max(1, Math.min(words.length, Math.round(phrase.length / 20)));
    // Cut at the word boundaries nearest each even share of the phrase's length.
    const ends = words.map((_, i) => words.slice(0, i + 1).join(" ").length);
    const cuts = Array.from({ length: pieces - 1 }, (_, k) => {
      const goal = ((k + 1) * phrase.length) / pieces;
      return ends.reduce((best, end, i) => (Math.abs(end - goal) < Math.abs(ends[best] - goal) ? i : best), 0);
    });
    let from = 0;
    for (const cut of [...new Set(cuts)].sort((a, b) => a - b)) {
      if (cut >= from && cut < words.length - 1) {
        chunks.push(words.slice(from, cut + 1).join(" "));
        from = cut + 1;
      }
    }
    chunks.push(words.slice(from).join(" "));
  }
  const total = chunks.reduce((sum, chunk) => sum + chunk.length + 2, 0);
  let at = delay;
  return chunks.map((chunk) => {
    const length = ((chunk.length + 2) / total) * speech;
    const cue = { start: at, end: at + length, text: chunk };
    at += length;
    return cue;
  });
}
