import { Img, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import setupEn from "../public/terminal/setup.json";
import setupRu from "../public/terminal/setup-ru.json";
import { useCopy, useLang } from "./lang";
import { Body, Code, Heading, Kicker, Rise, SidePanel } from "./parts";
import { TerminalPlayback, TerminalWindow, type Recording } from "./Terminal";
import { color, font, headingFont } from "./theme";

const RECORDINGS = { en: setupEn, ru: setupRu } as unknown as Record<string, Recording>;
const BEFORE = 0.05; // end a range just before the next marker's question appears

export function Intro() {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const line = spring({ frame: frame - 12, fps, config: { damping: 200 } });
  const copy = useCopy().intro;
  return (
    <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", paddingLeft: 180 }}>
      <div>
        <Rise>
          <Kicker>{copy.kicker}</Kicker>
        </Rise>
        <Rise delay={6}>
          <Heading size={112}>{copy.line1}</Heading>
        </Rise>
        <Rise delay={12}>
          <Heading size={112}>
            {copy.before}
            <span style={{ color: color.accent, fontStyle: "italic" }}>{copy.accent}</span>
            {copy.after}
          </Heading>
        </Rise>
        <div style={{ height: 3, width: 520 * line, background: color.accent, margin: "44px 0 34px", opacity: 0.8 }} />
        <Rise delay={24}>
          <Body style={{ fontSize: 36, color: color.muted }}>{copy.tagline}</Body>
        </Rise>
      </div>
    </div>
  );
}

function Card({ title, lines, delay }: { title: string; lines: string[]; delay: number }) {
  return (
    <Rise delay={delay} style={{ flex: 1 }}>
      <div
        style={{
          background: color.surface,
          border: `1px solid ${color.border}`,
          borderRadius: 22,
          padding: "44px 48px",
          height: 430,
        }}
      >
        <Heading size={46}>{title}</Heading>
        <div style={{ marginTop: 28 }}>
          {lines.map((line) => (
            <Body key={line} style={{ marginBottom: 12 }}>
              <span style={{ color: color.accent, marginRight: 14 }}>·</span>
              {line}
            </Body>
          ))}
        </div>
      </div>
    </Rise>
  );
}

export function Needs() {
  const copy = useCopy().needs;
  return (
    <div style={{ position: "absolute", inset: "150px 160px" }}>
      <Rise>
        <Kicker>{copy.kicker}</Kicker>
      </Rise>
      <Rise delay={4}>
        <Heading size={72}>{copy.title}</Heading>
      </Rise>
      <div style={{ display: "flex", gap: 48, marginTop: 64 }}>
        <Card delay={30} title={copy.server} lines={copy.serverLines} />
        <Card delay={150} title={copy.computer} lines={copy.computerLines} />
      </div>
    </div>
  );
}

function Typed({ text, start, perChar = 1.6 }: { text: string; start: number; perChar?: number }) {
  const frame = useCurrentFrame();
  const count = Math.max(0, Math.min(text.length, Math.floor((frame - start) / perChar)));
  return <>{text.slice(0, count)}</>;
}

export function Install() {
  const line = { fontFamily: font.mono, fontSize: 30, lineHeight: "52px", color: color.text };
  const copy = useCopy().install;
  return (
    <div style={{ position: "absolute", inset: "190px 200px" }}>
      <Rise>
        <Kicker>{copy.kicker}</Kicker>
      </Rise>
      <Rise delay={4} style={{ marginBottom: 54 }}>
        <Heading size={72}>{copy.title}</Heading>
      </Rise>
      <Rise delay={10}>
        <TerminalWindow title={copy.window}>
          <div style={line}>
            <span style={{ color: color.ok }}>$ </span>
            <Typed text="curl -sSf https://getmeridian.org/install.sh | bash" start={24} perChar={1.1} />
          </div>
          <div style={{ ...line, color: color.muted, marginTop: 20 }}>{copy.or}</div>
          <div style={line}>
            <span style={{ color: color.ok }}>$ </span>
            <Typed text="uv tool install meridian-vpn" start={130} perChar={1.3} />
          </div>
        </TerminalWindow>
      </Rise>
    </div>
  );
}

// Each terminal scene plays the recording from one marker to the next.
const RANGES: Record<string, [string, string]> = {
  server: ["", "roles"],
  roles: ["roles", "camouflage"],
  camouflage: ["camouflage", "users"],
  users: ["users", "apply"],
  apply: ["apply", "handoff"],
  handoff: ["handoff", ""],
};

export function SetupStep({ id }: { id: string }) {
  const step = useCopy().steps[id];
  const setup = RECORDINGS[useLang()];
  const [start, end] = RANGES[id];
  const from = start ? setup.markers[start] : 0;
  const to = end ? setup.markers[end] - BEFORE : setup.duration;
  return (
    <>
      <SidePanel step={step.step} title={step.title} hint={step.hint} />
      <div style={{ position: "absolute", left: 690, top: 56 }}>
        <TerminalPlayback recording={setup} from={from} to={to} />
      </div>
    </>
  );
}

export function Phone() {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  const lang = useLang();
  const copy = useCopy().phone;
  const width = 400;
  const screenHeight = 820;
  // Scroll by a share of the screenshot's own height (translateY % is relative to the image) to reach the QR code.
  const scroll = interpolate(frame, [60, durationInFrames - 70], [0, 42], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  return (
    <>
      <SidePanel step={copy.step} title={copy.title} hint={copy.hint} />
      <Rise delay={6} style={{ position: "absolute", left: 1060, top: 70 }}>
        <div
          style={{
            width: width + 28,
            height: screenHeight + 28,
            borderRadius: 64,
            background: "#0B0C10",
            padding: 14,
            boxShadow: "0 40px 90px rgba(0,0,0,0.55)",
            border: `2px solid ${color.border}`,
          }}
        >
          <div style={{ width, height: screenHeight, borderRadius: 52, overflow: "hidden", position: "relative" }}>
            <Img
              src={staticFile(`phone/page-qr.${lang}.png`)}
              style={{ width, transform: `translateY(${-scroll}%)` }}
            />
          </div>
        </div>
      </Rise>
    </>
  );
}

export function Later() {
  const copy = useCopy().later;
  return (
    <div style={{ position: "absolute", inset: "170px 200px" }}>
      <Rise>
        <Kicker>{copy.kicker}</Kicker>
      </Rise>
      <Rise delay={4} style={{ marginBottom: 56 }}>
        <Heading size={72}>{copy.title}</Heading>
      </Rise>
      {copy.commands.map(([command, what], index) => (
        <Rise key={command} delay={30 + index * 75}>
          <div
            style={{
              display: "flex",
              alignItems: "baseline",
              gap: 40,
              padding: "24px 34px",
              marginBottom: 18,
              background: color.surface,
              border: `1px solid ${color.border}`,
              borderRadius: 16,
            }}
          >
            <Code style={{ fontSize: 34, width: 700 }}>
              <span style={{ color: color.ok }}>$ </span>
              {command}
            </Code>
            <Body style={{ color: color.muted }}>{what}</Body>
          </div>
        </Rise>
      ))}
    </div>
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
  const copy = useCopy().why;
  const blocked = blockedAt !== undefined && frame >= blockedAt;
  const active = activeAt !== undefined && frame >= activeAt;
  return (
    <Rise delay={delay}>
      <div
        style={{
          width: 470,
          minHeight: 190,
          padding: "30px 36px",
          borderRadius: 18,
          background: color.surface,
          border: `2px solid ${blocked ? color.err : active ? color.ok : color.border}`,
          opacity: blocked ? 0.55 : 1,
        }}
      >
        <div
          style={{
            fontFamily: headingFont(lang),
            fontSize: 42,
            color: color.text,
            textDecoration: blocked ? "line-through" : "none",
          }}
        >
          {name}
        </div>
        <Body style={{ marginTop: 10, fontSize: 26 }}>{blocked ? copy.blocked : active ? copy.connected : what}</Body>
      </div>
    </Rise>
  );
}

export function Why() {
  const { durationInFrames } = useVideoConfig();
  const at = (share: number) => Math.round(durationInFrames * share); // follow the narration's sentences
  const copy = useCopy().why;
  return (
    <div style={{ position: "absolute", inset: "150px 160px" }}>
      <Rise>
        <Kicker>{copy.kicker}</Kicker>
      </Rise>
      <Rise delay={4}>
        <Heading size={66}>{copy.title}</Heading>
      </Rise>
      <Rise delay={20} style={{ marginTop: 30 }}>
        <Body style={{ fontSize: 32 }}>{copy.body}</Body>
      </Rise>
      <div style={{ display: "flex", gap: 40, marginTop: 70 }}>
        <Method delay={at(0.4)} name="Reality" what={copy.reality} blockedAt={at(0.74)} />
        <Method delay={at(0.4) + 15} name="XHTTP" what={copy.xhttp} activeAt={at(0.74) + 20} />
        <Method delay={at(0.4) + 30} name="Hysteria2" what={copy.hysteria} />
      </div>
    </div>
  );
}

export function Outro() {
  const copy = useCopy().outro;
  return (
    <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center" }}>
      <div style={{ textAlign: "center" }}>
        <Rise>
          <Heading size={120}>Meridian</Heading>
        </Rise>
        <Rise delay={8} style={{ marginTop: 26 }}>
          <Body style={{ fontSize: 40, color: color.accent }}>getmeridian.org</Body>
        </Rise>
        <Rise delay={16} style={{ marginTop: 40 }}>
          <Code style={{ fontSize: 30, color: color.muted }}>
            uv tool install meridian-vpn &amp;&amp; meridian setup
          </Code>
        </Rise>
        <Rise delay={30} style={{ marginTop: 80 }}>
          <Body style={{ fontSize: 22, color: color.muted }}>{copy.footer}</Body>
        </Rise>
      </div>
    </div>
  );
}
