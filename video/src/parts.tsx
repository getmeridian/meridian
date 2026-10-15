import type { CSSProperties, ReactNode } from "react";
import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { useLang } from "./lang";
import { captionCues } from "./timeline";
import { color, font, headingFont } from "./theme";

/** Fades a whole scene in and out so cuts never jump. */
export function Fade({ children }: { children: ReactNode }) {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  const opacity = interpolate(frame, [0, 10, durationInFrames - 10, durationInFrames], [0, 1, 1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  return <AbsoluteFill style={{ opacity }}>{children}</AbsoluteFill>;
}

export function Background() {
  return (
    <AbsoluteFill
      style={{
        background: `radial-gradient(1200px 700px at 18% 12%, ${color.accentSoft}, transparent 60%), ${color.bg}`,
      }}
    />
  );
}

/** Rises into place `delay` frames after the scene starts. */
export function Rise({ delay = 0, children, style }: { delay?: number; children: ReactNode; style?: CSSProperties }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const progress = spring({ frame: frame - delay, fps, config: { damping: 200 } });
  return (
    <div style={{ opacity: progress, transform: `translateY(${(1 - progress) * 24}px)`, ...style }}>{children}</div>
  );
}

/** Subtitles: the scene's sentences, each shown for its share of the narration. */
export function Caption({ text, speech }: { text: string; speech: number }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps;
  const current = captionCues(text, speech).find((cue) => t >= cue.start && t < cue.end)?.text;
  if (!current) return null;
  return (
    <div
      style={{
        position: "absolute",
        left: 0,
        right: 0,
        bottom: 34,
        display: "flex",
        justifyContent: "center",
      }}
    >
      <div
        style={{
          maxWidth: 1400,
          padding: "10px 22px",
          borderRadius: 10,
          background: "rgba(20,21,29,0.82)",
          color: color.text,
          fontFamily: font.sans,
          fontSize: 30,
          lineHeight: 1.3,
          textAlign: "center",
        }}
      >
        {current}
      </div>
    </div>
  );
}

export function Kicker({ children }: { children: ReactNode }) {
  return (
    <div
      style={{
        fontFamily: font.mono,
        fontSize: 20,
        letterSpacing: 2,
        textTransform: "uppercase",
        color: color.accent,
        marginBottom: 18,
      }}
    >
      {children}
    </div>
  );
}

export function Heading({ children, size = 64 }: { children: ReactNode; size?: number }) {
  return (
    <div
      style={{
        fontFamily: headingFont(useLang()),
        fontWeight: 600,
        fontSize: size,
        lineHeight: 1.08,
        color: color.text,
      }}
    >
      {children}
    </div>
  );
}

export function Body({ children, style }: { children: ReactNode; style?: CSSProperties }) {
  return (
    <div style={{ fontFamily: font.sans, fontSize: 30, lineHeight: 1.4, color: color.body, ...style }}>{children}</div>
  );
}

/** The left column beside a terminal or phone: step, title, one line of help. */
export function SidePanel({ step, title, hint }: { step: string; title: string; hint: string }) {
  return (
    <div style={{ position: "absolute", left: 96, top: 250, width: 520 }}>
      <Rise>
        <Kicker>{step}</Kicker>
      </Rise>
      <Rise delay={4}>
        <Heading size={58}>{title}</Heading>
      </Rise>
      <Rise delay={8} style={{ marginTop: 26 }}>
        <Body>{hint}</Body>
      </Rise>
    </div>
  );
}

export function Code({ children, style }: { children: ReactNode; style?: CSSProperties }) {
  return <span style={{ fontFamily: font.mono, color: color.text, ...style }}>{children}</span>;
}
