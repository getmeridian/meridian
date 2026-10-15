"""Generate background music locally with ACE-Step 1.5, cached by prompt hash.

    uv run --project ~/.cache/ace-step/ACE-Step-1.5 python scripts/music.py short

Reads music/<name>.json (caption, bpm, key, duration, seed) and writes
public/music/<name>.mp3 plus public/music/manifest.json. ACE-Step code and
weights are MIT-licensed, so the track can be committed and published. The
first run downloads the models into the ACE-Step checkout (several GB).
Set ACE_STEP_ROOT if the checkout lives elsewhere.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACE = Path(os.environ.get("ACE_STEP_ROOT", Path.home() / ".cache" / "ace-step" / "ACE-Step-1.5"))
os.environ["ACESTEP_LM_BACKEND"] = "mlx"
sys.path.insert(0, str(ACE))

from acestep.handler import AceStepHandler  # noqa: E402
from acestep.inference import GenerationConfig, GenerationParams, generate_music  # noqa: E402
from acestep.llm_inference import LLMHandler  # noqa: E402


def main(name: str) -> int:
    spec = json.loads((ROOT / "music" / f"{name}.json").read_text())
    out = ROOT / "public" / "music"
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    digest = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:12]
    if manifest.get(name, {}).get("hash") == digest and (out / f"{name}.mp3").exists():
        print(f"{name}: up to date")
        return 0

    dit = AceStepHandler()
    status, ok = dit.initialize_service(project_root=str(ACE), config_path="acestep-v15-turbo", device="auto")
    if not ok:
        raise SystemExit(f"ACE-Step DiT failed to load: {status}")
    llm = LLMHandler()
    status, ok = llm.initialize(
        checkpoint_dir=str(ACE / "checkpoints"), lm_model_path="acestep-5Hz-lm-1.7B", backend="mlx", device="auto"
    )
    if not ok:
        raise SystemExit(f"ACE-Step LM failed to load: {status}")

    params = GenerationParams(
        task_type="text2music",
        caption=spec["caption"],
        lyrics="[Instrumental]",
        instrumental=True,
        bpm=spec["bpm"],
        keyscale=spec.get("keyscale", ""),
        timesignature=spec.get("timesignature", ""),
        duration=spec["duration"],
        inference_steps=8,
        shift=3.0,
        seed=spec["seed"],
    )
    config = GenerationConfig(batch_size=1, use_random_seed=False, seeds=[spec["seed"]], audio_format="wav")
    with tempfile.TemporaryDirectory() as tmp:
        result = generate_music(dit, llm, params=params, config=config, save_dir=tmp)
        if not result.success:
            raise SystemExit(f"ACE-Step failed: {result.status_message}")
        wav = result.audios[0]["path"]
        target = out / f"{name}.mp3"
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", wav, "-ar", "44100", "-b:a", "160k", str(target)], check=True
        )
    probe = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(target)]
    seconds = round(float(subprocess.run(probe, capture_output=True, text=True, check=True).stdout), 3)
    manifest[name] = {"hash": digest, "seconds": seconds}
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"  ✓ {name} {seconds}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "short"))
