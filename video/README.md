# Meridian setup video

A 3–4 minute walkthrough of setting up a personal VPN with `meridian setup`, built with [Remotion](https://www.remotion.dev/) from a real recorded run.

## Reproduce

```bash
cd video
pnpm install

# 1. Record the wizard on a fresh lab VM (costs a few cents), then destroy it
source ~/.cache/mscripts/hcloud.env
uv run --group realvm python -m tests.realvm.lab up rec:hel1         # from the repo root
source ~/.cache/meridian-realvm/lab-<id>/env.sh
uv run --with pexpect --with pyte python video/recording/record.py setup "$VM_REC"
uv run --with pyte python video/recording/snapshots.py setup --address "$VM_REC"
uv run --group realvm python -m tests.realvm.lab down <id>

# 2. Phone screenshots of the demo connection page
uv run --with playwright python video/recording/phone.py

# 3. Narration (dry run first; --spend calls ElevenLabs for changed scenes only)
pnpm tts
pnpm tts --spend

# 4. Preview and render
pnpm studio
node scripts/stills.mjs              # one still per scene in out/stills/
pnpm render                          # out/meridian-setup.mp4
```

Edit the script in `narration/en.json`; scene visuals live in `src/scenes.tsx`.
Fonts are the website's (SIL Open Font License, see `public/fonts/licenses/`).
