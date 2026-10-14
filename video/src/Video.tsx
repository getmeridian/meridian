import { Audio } from "@remotion/media";
import type { ReactNode } from "react";
import { AbsoluteFill, Sequence, Series, staticFile } from "remotion";
import { Background, Caption, Fade } from "./parts";
import { Install, Intro, Later, Needs, Outro, Phone, SetupStep, Why } from "./scenes";
import { FPS } from "./theme";
import { scenes, VOICE_DELAY } from "./timeline";

const VISUALS: Record<string, () => ReactNode> = {
  intro: () => <Intro />,
  needs: () => <Needs />,
  install: () => <Install />,
  server: () => <SetupStep id="server" />,
  roles: () => <SetupStep id="roles" />,
  camouflage: () => <SetupStep id="camouflage" />,
  users: () => <SetupStep id="users" />,
  apply: () => <SetupStep id="apply" />,
  handoff: () => <SetupStep id="handoff" />,
  phone: () => <Phone />,
  later: () => <Later />,
  why: () => <Why />,
  outro: () => <Outro />,
};

export function Video() {
  return (
    <AbsoluteFill>
      <Background />
      <Series>
        {scenes.map((scene) => (
          <Series.Sequence key={scene.id} durationInFrames={scene.frames} name={scene.id}>
            <Fade>{VISUALS[scene.id]?.()}</Fade>
            {scene.id !== "intro" && scene.id !== "outro" ? (
              <Caption text={scene.text} seconds={scene.frames / FPS - VOICE_DELAY - 0.9} />
            ) : null}
            {scene.audio ? (
              <Sequence from={Math.round(VOICE_DELAY * FPS)}>
                <Audio src={staticFile(scene.audio)} />
              </Sequence>
            ) : null}
          </Series.Sequence>
        ))}
      </Series>
    </AbsoluteFill>
  );
}
