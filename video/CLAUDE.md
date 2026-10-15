# video — Remotion walkthrough of `meridian setup` (English and Russian)

## Design decisions

- **Real footage, replayed** — `recording/record.py` drives the real wizard on a lab VM through a pty and saves an asciicast to `~/.cache/meridian-video/raw/`; `recording/snapshots.py` replays it through pyte into `public/terminal/<name>.json`, which `src/Terminal.tsx` draws. No fake terminal output.
- **Markers cut the scenes** — recorder steps emit named markers (`server`, `roles`, `camouflage`, …); each terminal scene plays one marker range, sped up (shown as ×N) only when it would outrun its narration.
- **Two cuts, one composition** — `src/copy.ts` holds every on-screen word per language (export reads its chapter titles); `Setup`/`SetupRu` and `Thumbnail`/`ThumbnailRu` share the scenes, each with its own recording (`public/terminal/setup[-ru].json`) and phone shot.
- **Narration is the clock** — `narration/<lang>.json` holds the per-scene text; `public/audio/<lang>/manifest.json` records clip durations, and `src/timing.mjs` sizes every scene and caption cue from them (a reading-pace estimate until audio exists) for the composition and the scripts alike.
- **A vertical short beside the walkthrough** — `src/Short.tsx` (`Short`/`ShortRu`, 1080×1920) reuses the recordings through `TerminalCrop` (newest rows, cropped to a phone-readable width), has its own narration (`narration/short-<lang>.json`, `SHORT_PACE`) and phrase captions (`chunkCues`), and keeps text above y≈1480 and off the right edge where TikTok draws its buttons.
- **Music is generated, not licensed** — `scripts/music.py` runs ACE-Step 1.5 (MIT) from `music/<name>.json` with a fixed seed into `public/music/`; the short ducks it under every narration line.
- **One voice engine, a native voice per language** — `scripts/tts.mjs` voices every narration with ElevenLabs `eleven_v4`: English with Eric, Russian with Stanislav (a native library voice; Eric isn't verified for Russian). Each short shares its walkthrough's voice; `pronounce` respells Latin terms in Cyrillic for the voice only, captions keep the original.
- **One export step** — `scripts/export.mjs` turns a render into the SRT (the burned-in cues), YouTube chapters, the thumbnail, and the landing page's 720p copy and poster in `website/public/video/`; `youtube.md` holds the upload text.
- **Credits are spent once** — `tts.mjs` is a dry run unless `--spend`, and regenerates only scenes whose spoken text or voice settings hash changed; the MP3s are committed so re-renders cost nothing.
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
- **Keep the ElevenLabs key out of the repo** — it lives in `~/.cache/meridian-video/elevenlabs.env`. The plan is paid (Starter, 30,000 credits a month): audio made on it is commercial and needs no credit, but audio made outside a paid subscription never is, so regenerate rather than reuse old clips.
- **Only a complete take feeds `snapshots.py`** — rehearsals save `*.rehearsal.cast` and failed steps `*.failed.cast`; a rehearsal once overwrote the real cast name.
- **Never resume a take on camera** — a transient SSH stall failed one apply mid-take; recreate the VM and record again rather than showing the wizard resume.
- **npm and pnpm need the proxy variables unset** — `env -u HTTPS_PROXY -u HTTP_PROXY -u https_proxy -u http_proxy pnpm install`.

## Links

- How to reproduce: `README.md`
- Published uploads and their text: `youtube.md`
- Lab VMs: `tests/realvm/lab.py`
