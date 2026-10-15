import type { CSSProperties } from "react";
import { useCurrentFrame, useVideoConfig } from "remotion";
import { color, font } from "./theme";

type Run = [string, number];
type Frame = { t: number; lines: number[]; cursor: [number, number, boolean] }; // lines index into rows
type Style = { fg: string; bg: string; bold: boolean; italic: boolean; underline: boolean; reverse: boolean };
export type Recording = {
  width: number;
  height: number;
  duration: number;
  markers: Record<string, number>;
  styles: Style[];
  rows: Run[][];
  frames: Frame[];
};

// pyte's colour names, tuned to the site palette (Rich prints yellow as pyte's "brown").
const PALETTE: Record<string, string> = {
  black: "#3A3E52",
  red: "#F06060",
  green: "#5CE88E",
  brown: "#D4A24E",
  blue: "#7AA2F7",
  magenta: "#C792EA",
  cyan: "#7FD1C7",
  white: "#E4E2DE",
  brightblack: "#8A8FA3",
  brightred: "#FF8080",
  brightgreen: "#8AF2B0",
  brightyellow: "#F0D9A8",
  brightblue: "#9DBBFA",
  brightmagenta: "#DDB6F2",
  brightcyan: "#A6E3DA",
  brightwhite: "#FFFFFF",
};

const paint = (value: string, fallback: string) =>
  value === "default" ? fallback : (PALETTE[value] ?? (/^[0-9a-f]{6}$/i.test(value) ? `#${value}` : fallback));

function frameAt(frames: Frame[], t: number): Frame {
  let low = 0;
  let high = frames.length - 1;
  while (low < high) {
    const mid = (low + high + 1) >> 1;
    if (frames[mid].t <= t) low = mid;
    else high = mid - 1;
  }
  return frames[low];
}

/**
 * Plays recording time [from, to) inside the current sequence: faster when it would not fit,
 * and starting late when it is short, so it ends near where the narration explains it.
 */
export function TerminalPlayback({
  recording,
  from,
  to,
  fontSize = 18,
  title = "meridian setup",
}: {
  recording: Recording;
  from: number;
  to: number;
  fontSize?: number;
  title?: string;
}) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const { t, speed } = usePlayback(from, to);
  const shown = frameAt(recording.frames, t);
  const lineHeight = Math.round(fontSize * 1.42);
  const [cx, cy, cursorOn] = shown.cursor;
  const blink = Math.floor(frame / (fps / 2)) % 2 === 0;

  return (
    <TerminalWindow title={title} speed={speed}>
      <div
        style={{
          position: "relative",
          width: `${recording.width * 0.6}em`,
          fontFamily: font.mono,
          fontSize,
          lineHeight: `${lineHeight}px`,
        }}
      >
        {Array.from({ length: recording.height }, (_, y) => (
          <div key={y} style={{ height: lineHeight, whiteSpace: "pre" }}>
            {(recording.rows[shown.lines[y]] ?? []).map(([text, index], i) => (
              <span key={i} style={runStyle(recording.styles[index])}>
                {text}
              </span>
            ))}
          </div>
        ))}
        {cursorOn && blink ? (
          <div
            style={{
              position: "absolute",
              left: `${cx * 0.6}em`,
              top: cy * lineHeight + 3,
              width: "0.6em",
              height: lineHeight - 6,
              background: color.accent,
              opacity: 0.85,
            }}
          />
        ) : null}
      </div>
    </TerminalWindow>
  );
}

/**
 * Recording time for the current frame when [from, to) plays inside the current sequence:
 * faster when it would not fit, and starting late when it is short, so it ends near where
 * the narration explains it.
 */
function usePlayback(from: number, to: number, rest = 1.0) {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  const available = Math.max(durationInFrames / fps - rest, 1); // leave a beat to read the result
  const speed = Math.max(1, (to - from) / available);
  const lead = Math.max(0, available * 0.75 - (to - from));
  return { t: Math.min(from + Math.max(0, frame / fps - lead) * speed, to), speed };
}

/**
 * The vertical cut's terminal: the newest `rows` lines down to the cursor, `cols` wide, at a size a
 * phone can read. Lines longer than the crop fade out at the right edge instead of wrapping.
 */
export function TerminalCrop({
  recording,
  from,
  to,
  cols = 54,
  rows = 14,
  fontSize = 28,
  title = "meridian setup",
}: {
  recording: Recording;
  from: number;
  to: number;
  cols?: number;
  rows?: number;
  fontSize?: number;
  title?: string;
}) {
  const { t, speed } = usePlayback(from, to, 0.6);
  const shown = frameAt(recording.frames, t);
  const lineHeight = Math.round(fontSize * 1.42);
  const filled = (y: number) => (recording.rows[shown.lines[y]] ?? []).some(([text]) => text.trim() !== "");
  let last = shown.cursor[2] ? shown.cursor[1] : -1;
  for (let y = recording.height - 1; y > last; y--) {
    if (filled(y)) {
      last = y;
      break;
    }
  }
  const first = Math.max(0, last - rows + 1);
  return (
    <TerminalWindow title={title} speed={speed}>
      <div
        style={{
          width: `${cols * 0.6}em`,
          height: rows * lineHeight,
          overflow: "hidden",
          fontFamily: font.mono,
          fontSize,
          lineHeight: `${lineHeight}px`,
          maskImage: "linear-gradient(to right, black 88%, transparent)",
        }}
      >
        {Array.from({ length: last - first + 1 }, (_, i) => (
          <div key={i} style={{ height: lineHeight, whiteSpace: "pre" }}>
            {(recording.rows[shown.lines[first + i]] ?? []).map(([text, index], j) => (
              <span key={j} style={runStyle(recording.styles[index])}>
                {text}
              </span>
            ))}
          </div>
        ))}
      </div>
    </TerminalWindow>
  );
}

function runStyle(style: Style | undefined): CSSProperties {
  if (!style) return {};
  let fg = paint(style.fg, color.text);
  let bg = paint(style.bg, "transparent");
  if (style.reverse) [fg, bg] = [bg === "transparent" ? color.terminal : bg, fg];
  return {
    color: fg,
    background: bg,
    fontWeight: style.bold ? 600 : 400,
    fontStyle: style.italic ? "italic" : "normal",
    textDecoration: style.underline ? "underline" : "none",
  };
}

export function TerminalWindow({
  title,
  children,
  speed = 1,
  style,
}: {
  title: string;
  children: React.ReactNode;
  speed?: number;
  style?: CSSProperties;
}) {
  return (
    <div
      style={{
        background: color.terminal,
        border: `1px solid ${color.border}`,
        borderRadius: 18,
        boxShadow: "0 30px 80px rgba(0,0,0,0.45)",
        overflow: "hidden",
        ...style,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 9,
          padding: "13px 18px",
          background: color.surface,
          borderBottom: `1px solid ${color.border}`,
          fontFamily: font.sans,
          fontSize: 17,
          color: color.muted,
        }}
      >
        {["#F06060", "#D4A24E", "#5CE88E"].map((dot) => (
          <span key={dot} style={{ width: 13, height: 13, borderRadius: 7, background: dot, opacity: 0.8 }} />
        ))}
        <span style={{ marginLeft: 14, flex: 1 }}>{title}</span>
        {speed > 1.5 ? (
          <span style={{ fontFamily: font.mono, fontSize: 15, color: color.accent }}>×{Math.round(speed)}</span>
        ) : null}
      </div>
      <div style={{ padding: "16px 22px 18px" }}>{children}</div>
    </div>
  );
}
