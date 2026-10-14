# video — Remotion walkthrough of `meridian setup`

## Design decisions

- **Real footage, replayed** — `recording/record.py` drives the real wizard on a lab VM through a pty and saves an asciicast to `~/.cache/meridian-video/raw/`; `recording/snapshots.py` replays it through pyte into `public/terminal/<name>.json`, which `src/Terminal.tsx` draws. No fake terminal output.
- **Markers cut the scenes** — recorder steps emit named markers (`server`, `roles`, `camouflage`, …); each terminal scene plays one marker range, sped up (shown as ×N) only when it would outrun its narration.
- **Narration is the clock** — `narration/<lang>.json` holds voice, model, and per-scene text; `scripts/tts.mjs` writes `public/audio/<lang>/<id>.mp3` plus `manifest.json` durations, and `src/timeline.ts` sizes every scene from them (a reading-pace estimate until audio exists).
- **Credits are spent once** — TTS is a dry run unless `--spend`, and regenerates only scenes whose text, voice, model, or settings hash changed; the MP3s are committed so re-renders cost nothing.
- **Website look** — palette from `website/src/styles/tokens.css`, the same self-hosted fonts and unicode ranges (`src/theme.ts`); renders with the local Chrome (`remotion.config.ts`).
- **Phone shots are demo data** — `recording/phone.py` screenshots `meridian dev preview --name Mom` on an RFC 5737 address.

## What's done well

- **Sanitize before commit** — every screen line passes `sanitize()`: the VM address and its typed prefixes become `198.51.100.7`, unknown hostnames become `.example` names, long tokens become same-length dummies, and the script refuses to write if any other IPv4 survives.
- **Same supply-chain guardrails as the website** — `pnpm-workspace.yaml` copies the release-age, exotic-subdependency, and trust policies; Remotion packages are pinned to one exact version.

## Pitfalls

- **Answer prompt_toolkit's cursor-position request** — without a reply to `ESC[6n` the wizard prints a CPR warning into the footage.
- **Run `.venv/bin/meridian`, not `uv run`** — uv prints a `VIRTUAL_ENV` mismatch warning into the recording.
- **Neighbour camouflage sites are real third-party domains** — never commit a raw cast or skip `snapshots.py`.
- **Clear the lab's `MERIDIAN_HOME` before a take** — a saved draft or server makes the wizard resume and the recorder's waits never match.
- **`--stop-at review` rehearses without applying** — use it while changing recorder steps.
- **Keep the ElevenLabs key out of the repo** — it lives in `~/.cache/meridian-video/elevenlabs.env`; the free tier needs attribution (the outro credits ElevenLabs).
- **npm and pnpm need the proxy variables unset** — `env -u HTTPS_PROXY -u HTTP_PROXY -u https_proxy -u http_proxy pnpm install`.

## Links

- How to reproduce: `README.md`
- Lab VMs: `tests/realvm/lab.py`
