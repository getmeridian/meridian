import { Audio } from "@remotion/media";
import type { ReactNode } from "react";
import { AbsoluteFill, Sequence, Series, staticFile } from "remotion";
import { Background, Caption, Fade } from "./parts";
import { Install, Intro, Later, Needs, Outro, Phone, SetupStep, Why } from "./scenes";
import type { Lang } from "./copy";
import { LangContext } from "./lang";
import { FPS, scenesFor, VOICE_DELAY } from "./timeline";

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

export function Video({ lang }: { lang: Lang }) {
  return (
    <LangContext.Provider value={lang}>
      <AbsoluteFill>
        <Background />
        <Series>
          {scenesFor(lang).map((scene) => (
            <Series.Sequence key={scene.id} durationInFrames={scene.frames} name={scene.id}>
              <Fade>{VISUALS[scene.id]?.()}</Fade>
              {scene.id !== "intro" && scene.id !== "outro" ? (
                <Caption text={scene.text} speech={scene.speech} />
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
    </LangContext.Provider>
  );
}
