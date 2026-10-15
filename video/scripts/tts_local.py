"""Generate narration locally with Qwen3-TTS on Apple Silicon, cached by text hash.

    uv run --python 3.12 --with mlx-audio --with soundfile python scripts/tts_local.py ru

For narration files with "engine": "qwen3". No credits and no network after the
first model download. The first scene is voiced from the `instruct` description
(VoiceDesign) and becomes the reference that the Base model clones for every
other scene, so the whole video keeps one voice. Writes the same
public/audio/<lang>/<id>.mp3 and manifest.json as scripts/tts.mjs.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import mlx.core as mx
import numpy as np
import soundfile as sf
from mlx_audio.tts.utils import load_model

ROOT = Path(__file__).resolve().parents[1]


def spoken(text: str, pronounce: dict[str, str]) -> str:
    """Spell Latin terms the way the narrator should say them; captions keep the original text."""
    for term in sorted(pronounce, key=len, reverse=True):
        text = text.replace(term, pronounce[term])
    return text


def fingerprint(*parts: object) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()[:12]


def save_mp3(audio: np.ndarray, rate: int, path: Path) -> float:
    with tempfile.NamedTemporaryFile(suffix=".wav") as wav:
        sf.write(wav.name, audio, rate)
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", wav.name, "-ar", "44100", "-b:a", "128k", str(path)],
            check=True,
        )
    probe = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)]
    return round(float(subprocess.run(probe, capture_output=True, text=True, check=True).stdout), 3)


def main(lang: str) -> int:
    narration = json.loads((ROOT / "narration" / f"{lang}.json").read_text())
    if narration.get("engine") != "qwen3":
        raise SystemExit(f"narration/{lang}.json is not a qwen3 narration; use scripts/tts.mjs")
    qwen = narration["qwen3"]
    pronounce = narration.get("pronounce", {})
    out = ROOT / "public" / "audio" / lang
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}

    reference, *rest = narration["scenes"]
    reference_text = spoken(reference["text"], pronounce)
    reference_hash = fingerprint(reference_text, qwen["design"], qwen["instruct"], qwen["seed"])
    if manifest.get(reference["id"], {}).get("hash") != reference_hash:
        mx.random.seed(qwen["seed"])
        model = load_model(qwen["design"])
        results = model.generate_voice_design(text=reference_text, language=qwen["language"], instruct=qwen["instruct"])
        audio = np.concatenate([np.array(result.audio) for result in results])
        seconds = save_mp3(audio, model.sample_rate, out / f"{reference['id']}.mp3")
        manifest[reference["id"]] = {"hash": reference_hash, "seconds": seconds}
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        print(f"  ✓ {reference['id']} {seconds}s (voice reference)")

    stale = [
        scene
        for scene in rest
        if manifest.get(scene["id"], {}).get("hash")
        != fingerprint(spoken(scene["text"], pronounce), qwen["clone"], qwen["seed"], reference_hash)
    ]
    print(f"{len(stale)} of {len(rest)} cloned scenes to generate")
    if not stale:
        return 0
    model = load_model(qwen["clone"])
    with tempfile.NamedTemporaryFile(suffix=".wav") as ref:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(out / f"{reference['id']}.mp3"), "-ar", "24000", ref.name],
            check=True,
        )
        for scene in stale:
            text = spoken(scene["text"], pronounce)
            mx.random.seed(qwen["seed"])
            results = model.generate(
                text=text, ref_audio=ref.name, ref_text=reference_text, lang_code=qwen["language"].lower()
            )
            audio = np.concatenate([np.array(result.audio) for result in results])
            seconds = save_mp3(audio, model.sample_rate, out / f"{scene['id']}.mp3")
            manifest[scene["id"]] = {
                "hash": fingerprint(text, qwen["clone"], qwen["seed"], reference_hash),
                "seconds": seconds,
            }
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")  # keep progress if a later scene fails
            print(f"  ✓ {scene['id']} {seconds}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "ru"))
