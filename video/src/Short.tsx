import { Audio } from "@remotion/media";
import type { ReactNode } from "react";
import {
  AbsoluteFill,
  Img,
  interpolate,
  Sequence,
  Series,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import setupEn from "../public/terminal/setup.json";
import setupRu from "../public/terminal/setup-ru.json";
import type { Lang } from "./copy";
import { LangContext, useCopy, useLang } from "./lang";
import { Background, Body, Code, Heading, Kicker, Rise } from "./parts";
import { TerminalCrop, TerminalWindow, type Recording } from "./Terminal";
import { chunkCues, FPS, shortScenesFor } from "./timeline";
import { color, font, headingFont } from "./theme";

// The vertical (9:16) cut for TikTok, Shorts, and Reels. Text stays inside the band TikTok's own
// buttons leave free: nothing important below y=1480 or in the right 140 px.
export const SHORT_WIDTH = 1080;
export const SHORT_HEIGHT = 1920;
const SIDE = 70;
const CAPTION_TOP = 1330;

const RECORDINGS = { en: setupEn, ru: setupRu } as unknown as Record<string, Recording>;
const BEFORE = 0.05;

function range(recording: Recording, start: string, end: string): [number, number] {
  return [start ? recording.markers[start] : 0, end ? recording.markers[end] - BEFORE : recording.duration];
}

/** Quick in-and-out so a 55-second cut never jumps. */
function Cut({ children }: { children: ReactNode }) {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  const opacity = interpolate(frame, [0, 5, durationInFrames - 5, durationInFrames], [0, 1, 1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  return <AbsoluteFill style={{ opacity }}>{children}</AbsoluteFill>;
}

function Title({ kicker, title }: { kicker: string; title?: string }) {
  return (
    <div style={{ position: "absolute", left: SIDE, right: SIDE + 70, top: 190 }}>
      <Rise>
        <Kicker>{kicker}</Kicker>
      </Rise>
      {title ? (
        <Rise delay={3}>
          <Heading size={84}>{title}</Heading>
        </Rise>
      ) : null}
    </div>
  );
}

function Stage({ children, top = 470 }: { children: ReactNode; top?: number }) {
  return (
    <Rise delay={5} style={{ position: "absolute", left: 0, right: 0, top, display: "flex", justifyContent: "center" }}>
      {children}
    </Rise>
  );
}

function PhoneShot({ height, scrollTo = 0, delay = 0 }: { height: number; scrollTo?: number; delay?: number }) {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  const lang = useLang();
  const width = Math.round(height * 0.47);
  const scroll = interpolate(frame, [delay + 20, durationInFrames - 20], [0, scrollTo], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  return (
    <div
      style={{
        width: width + 28,
        height: height + 28,
        borderRadius: 70,
        background: "#0B0C10",
        padding: 14,
        boxShadow: "0 40px 90px rgba(0,0,0,0.55)",
        border: `2px solid ${color.border}`,
      }}
    >
      <div style={{ width, height, borderRadius: 58, overflow: "hidden" }}>
        <Img src={staticFile(`phone/page-qr.${lang}.png`)} style={{ width, transform: `translateY(${-scroll}%)` }} />
      </div>
    </div>
  );
}

function Hook() {
  const copy = useCopy().short.hook;
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const rise = spring({ frame: frame - 8, fps, config: { damping: 200 } });
  return (
    <>
      <div style={{ position: "absolute", left: SIDE, right: SIDE + 70, top: 230 }}>
        <Rise>
          <Heading size={128}>{copy.line1}</Heading>
        </Rise>
        <Rise delay={4}>
          <Heading size={128}>
            {copy.before}
            <span style={{ color: color.accent, fontStyle: "italic" }}>{copy.accent}</span>
          </Heading>
        </Rise>
        <Rise delay={10} style={{ marginTop: 34 }}>
          <Body style={{ fontSize: 44, color: color.muted }}>{copy.sub}</Body>
        </Rise>
      </div>
      <div
        style={{
          position: "absolute",
          left: 0,
          right: 0,
          top: 780,
          display: "flex",
          justifyContent: "center",
          transform: `translateY(${(1 - rise) * 500}px)`,
        }}
      >
        <PhoneShot height={900} />
      </div>
    </>
  );
}

function NeedCard({ title, line, delay }: { title: string; line: string; delay: number }) {
  return (
    <Rise delay={delay}>
      <div
        style={{
          width: 870,
          padding: "40px 46px",
          marginBottom: 34,
          borderRadius: 26,
          background: color.surface,
          border: `1px solid ${color.border}`,
        }}
      >
        <Heading size={64}>{title}</Heading>
        <Body style={{ fontSize: 36, marginTop: 14 }}>{line}</Body>
      </div>
    </Rise>
  );
}

function Needs() {
  const copy = useCopy().short.needs;
  return (
    <>
      <Title kicker={copy.kicker} />
      <div style={{ position: "absolute", left: SIDE, top: 420 }}>
        <NeedCard delay={4} title={copy.server} line={copy.serverLine} />
        <NeedCard delay={30} title={copy.laptop} line={copy.laptopLine} />
      </div>
    </>
  );
}

function Typed({ text, start, perChar }: { text: string; start: number; perChar: number }) {
  const frame = useCurrentFrame();
  return <>{text.slice(0, Math.max(0, Math.min(text.length, Math.floor((frame - start) / perChar))))}</>;
}

function Install() {
  const copy = useCopy().short.install;
  const line = {
    fontFamily: font.mono,
    fontSize: 27,
    lineHeight: "50px",
    color: color.text,
    whiteSpace: "pre" as const,
  };
  return (
    <>
      <Title kicker={copy.kicker} title={copy.title} />
      <Stage>
        <TerminalWindow title="Terminal" style={{ width: 940 }}>
          <div style={line}>
            <span style={{ color: color.ok }}>$ </span>
            <Typed text="curl -sSf https://getmeridian.org/install.sh | bash" start={8} perChar={0.9} />
          </div>
          <div style={{ ...line, marginTop: 14 }}>
            <span style={{ color: color.ok }}>$ </span>
            <Typed text="meridian setup" start={70} perChar={2} />
          </div>
        </TerminalWindow>
      </Stage>
    </>
  );
}

function TerminalScene({ kicker, title, start, end }: { kicker: string; title: string; start: string; end: string }) {
  const recording = RECORDINGS[useLang()];
  const [from, to] = range(recording, start, end);
  return (
    <>
      <Title kicker={kicker} title={title} />
      <Stage>
        <TerminalCrop recording={recording} from={from} to={to} rows={17} />
      </Stage>
    </>
  );
}

function Apply() {
  const copy = useCopy().short.apply;
  const recording = RECORDINGS[useLang()];
  const { durationInFrames } = useVideoConfig();
  const [from, to] = range(recording, "apply", "handoff");
  return (
    <>
      <Title kicker={copy.kicker} title={copy.title} />
      <div
        style={{
          position: "absolute",
          left: SIDE,
          right: SIDE + 70,
          top: 400,
          display: "flex",
          flexWrap: "wrap",
          gap: 14,
        }}
      >
        {copy.items.map((item, index) => (
          <Rise key={item} delay={10 + Math.round((index * durationInFrames) / (copy.items.length + 1))}>
            <span
              style={{
                display: "inline-block",
                padding: "10px 22px",
                borderRadius: 999,
                background: color.accentSoft,
                border: `1px solid ${color.accent}`,
                fontFamily: font.sans,
                fontSize: 30,
                color: color.text,
              }}
            >
              <span style={{ color: color.ok, marginRight: 10 }}>✓</span>
              {item}
            </span>
          </Rise>
        ))}
      </div>
      <Stage top={560}>
        <TerminalCrop recording={recording} from={from} to={to} rows={15} />
      </Stage>
    </>
  );
}

function Phone() {
  const copy = useCopy().short.phone;
  return (
    <>
      <Title kicker={copy.kicker} title={copy.title} />
      <Stage top={400}>
        <PhoneShot height={860} scrollTo={42} delay={5} />
      </Stage>
    </>
  );
}

function Method({
  name,
  what,
  delay,
  blockedAt,
  activeAt,
}: {
  name: string;
  what: string;
  delay: number;
  blockedAt?: number;
  activeAt?: number;
}) {
  const frame = useCurrentFrame();
  const lang = useLang();
  const why = useCopy().why;
  const blocked = blockedAt !== undefined && frame >= blockedAt;
  const active = activeAt !== undefined && frame >= activeAt;
  return (
    <Rise delay={delay}>
      <div
        style={{
          width: 870,
          padding: "26px 38px",
          marginBottom: 24,
          borderRadius: 22,
          background: color.surface,
          border: `3px solid ${blocked ? color.err : active ? color.ok : color.border}`,
          opacity: blocked ? 0.55 : 1,
        }}
      >
        <div
          style={{
            fontFamily: headingFont(lang),
            fontSize: 50,
            color: color.text,
            textDecoration: blocked ? "line-through" : "none",
          }}
        >
          {name}
        </div>
        <Body style={{ fontSize: 32, marginTop: 6 }}>{blocked ? why.blocked : active ? why.connected : what}</Body>
      </div>
    </Rise>
  );
}

function Why() {
  const copy = useCopy();
  const { durationInFrames } = useVideoConfig();
  const at = (share: number) => Math.round(durationInFrames * share);
  return (
    <>
      <Title kicker={copy.short.why.kicker} title={copy.short.why.title} />
      <div style={{ position: "absolute", left: SIDE, top: 450 }}>
        <Method delay={6} name="Reality" what={copy.why.reality} blockedAt={at(0.55)} />
        <Method delay={12} name="XHTTP" what={copy.why.xhttp} activeAt={at(0.55) + 12} />
        <Method delay={18} name="Hysteria2" what={copy.why.hysteria} />
      </div>
    </>
  );
}

function Cta() {
  const copy = useCopy().short.cta;
  return (
    <div style={{ position: "absolute", left: SIDE, right: SIDE + 70, top: 560, textAlign: "center" }}>
      <Rise>
        <Heading size={140}>Meridian</Heading>
      </Rise>
      <Rise delay={6} style={{ marginTop: 30 }}>
        <Body style={{ fontSize: 56, color: color.accent }}>getmeridian.org</Body>
      </Rise>
      <Rise delay={12} style={{ marginTop: 44 }}>
        <Code style={{ fontSize: 34, color: color.muted }}>meridian setup</Code>
      </Rise>
      <Rise delay={18} style={{ marginTop: 50 }}>
        <Body style={{ fontSize: 34 }}>{copy.more}</Body>
      </Rise>
    </div>
  );
}

function Footer() {
  const copy = useCopy().short.cta;
  return (
    <Rise delay={24} style={{ position: "absolute", left: SIDE, right: SIDE + 70, top: 1420, textAlign: "center" }}>
      <Body style={{ fontSize: 24, color: color.muted }}>{copy.footer}</Body>
    </Rise>
  );
}

/** Big phrase-by-phrase captions in the free band above TikTok's buttons. */
function Captions({ text, speech, delay }: { text: string; speech: number; delay: number }) {
  const frame = useCurrentFrame();
  const t = frame / FPS;
  const cue = chunkCues(text, speech, delay).find((c) => t >= c.start && t < c.end);
  if (!cue) return null;
  const pop = spring({ frame: frame - Math.round(cue.start * FPS), fps: FPS, config: { damping: 18, mass: 0.4 } });
  return (
    <div
      style={{
        position: "absolute",
        left: SIDE,
        right: SIDE + 70,
        top: CAPTION_TOP,
        display: "flex",
        justifyContent: "center",
      }}
    >
      <div
        style={{
          padding: "14px 30px",
          borderRadius: 18,
          background: "rgba(14,15,21,0.86)",
          fontFamily: font.sans,
          fontWeight: 700,
          fontSize: 58,
          lineHeight: 1.15,
          color: color.text,
          textAlign: "center",
          transform: `scale(${0.92 + 0.08 * pop})`,
        }}
      >
        {cue.text}
      </div>
    </div>
  );
}

const VISUALS: Record<string, () => ReactNode> = {
  hook: () => <Hook />,
  needs: () => <Needs />,
  install: () => <Install />,
  wizard: () => <WizardScene />,
  camouflage: () => <CamouflageScene />,
  apply: () => <Apply />,
  phone: () => <Phone />,
  why: () => <Why />,
  cta: () => <Cta />,
};

function WizardScene() {
  const copy = useCopy().short.wizard;
  return <TerminalScene kicker={copy.kicker} title={copy.title} start="" end="camouflage" />;
}

function CamouflageScene() {
  const copy = useCopy().short.camouflage;
  return <TerminalScene kicker={copy.kicker} title={copy.title} start="camouflage" end="users" />;
}

/** Music under the voice: louder in the gaps, ducked while someone speaks, faded at both ends. */
function musicVolume(frame: number, speech: [number, number][], total: number) {
  const ramp = 8;
  const distance = Math.min(
    ...speech.map(([start, end]) => (frame < start ? start - frame : frame > end ? frame - end : 0)),
  );
  const duck = interpolate(distance, [0, ramp], [0.13, 0.3], { extrapolateRight: "clamp" });
  const edges = interpolate(frame, [0, 12, total - 45, total], [0, 1, 1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  return duck * edges;
}

export function Short({ lang }: { lang: Lang }) {
  const scenes = shortScenesFor(lang);
  const total = scenes.reduce((sum, scene) => sum + scene.frames, 0);
  const speech = scenes.map((scene): [number, number] => [
    scene.start + Math.round(scene.delay * FPS),
    scene.start + Math.round((scene.delay + scene.speech) * FPS),
  ]);
  return (
    <LangContext.Provider value={lang}>
      <AbsoluteFill>
        <Background />
        <Audio src={staticFile("music/short.mp3")} volume={(frame) => musicVolume(frame, speech, total)} />
        <Series>
          {scenes.map((scene) => (
            <Series.Sequence key={scene.id} durationInFrames={scene.frames} name={scene.id}>
              <Cut>{VISUALS[scene.id]?.()}</Cut>
              {scene.id === "cta" ? (
                <Footer />
              ) : (
                <Captions text={scene.text} speech={scene.speech} delay={scene.delay} />
              )}
              {scene.audio ? (
                <Sequence from={Math.round(scene.delay * FPS)}>
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
