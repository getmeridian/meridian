import { Img, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import setupRecording from "../public/terminal/setup.json";
import { Body, Code, Heading, Kicker, Rise, SidePanel } from "./parts";
import { TerminalPlayback, TerminalWindow, type Recording } from "./Terminal";
import { color, font } from "./theme";

const setup = setupRecording as unknown as Recording;
const mark = (name: string) => setup.markers[name] ?? 0;
const BEFORE = 0.05; // end a range just before the next marker's question appears

export function Intro() {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const line = spring({ frame: frame - 12, fps, config: { damping: 200 } });
  return (
    <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", paddingLeft: 180 }}>
      <div>
        <Rise>
          <Kicker>Meridian · free and open source</Kicker>
        </Rise>
        <Rise delay={6}>
          <Heading size={112}>Your own VPN,</Heading>
        </Rise>
        <Rise delay={12}>
          <Heading size={112}>
            in about <span style={{ color: color.accent, fontStyle: "italic" }}>ten minutes</span>.
          </Heading>
        </Rise>
        <div style={{ height: 3, width: 520 * line, background: color.accent, margin: "44px 0 34px", opacity: 0.8 }} />
        <Rise delay={24}>
          <Body style={{ fontSize: 36, color: color.muted }}>Deploy it right. Share it easily.</Body>
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
          height: 360,
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
  return (
    <div style={{ position: "absolute", inset: "150px 160px" }}>
      <Rise>
        <Kicker>What you need</Kicker>
      </Rise>
      <Rise delay={4}>
        <Heading size={72}>Two things.</Heading>
      </Rise>
      <div style={{ display: "flex", gap: 48, marginTop: 64 }}>
        <Card
          delay={30}
          title="A small rented server"
          lines={["A VPS with Debian or Ubuntu", "The cheapest plan is plenty", "In the country you want to browse from"]}
        />
        <Card
          delay={150}
          title="A computer with a terminal"
          lines={["Where you can log in to the server over SSH", "That's where Meridian runs"]}
        />
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
  return (
    <div style={{ position: "absolute", inset: "190px 200px" }}>
      <Rise>
        <Kicker>Install</Kicker>
      </Rise>
      <Rise delay={4} style={{ marginBottom: 54 }}>
        <Heading size={72}>One line on your computer.</Heading>
      </Rise>
      <Rise delay={10}>
        <TerminalWindow title="Terminal">
          <div style={line}>
            <span style={{ color: color.ok }}>$ </span>
            <Typed text="curl -sSf https://getmeridian.org/install.sh | bash" start={24} perChar={1.1} />
          </div>
          <div style={{ ...line, color: color.muted, marginTop: 20 }}># or, if you already use uv:</div>
          <div style={line}>
            <span style={{ color: color.ok }}>$ </span>
            <Typed text="uv tool install meridian-vpn" start={130} perChar={1.3} />
          </div>
        </TerminalWindow>
      </Rise>
    </div>
  );
}

const STEPS: Record<string, { step: string; title: string; hint: string; from: string; to: string }> = {
  server: {
    step: "Step 1 · Servers",
    title: "Run meridian setup",
    hint: "Arrow keys and Enter. ? explains a step, Esc goes back.",
    from: "",
    to: "roles",
  },
  roles: {
    step: "Step 2 · Roles",
    title: "Defaults do the work",
    hint: "One server runs the panel and carries the traffic.",
    from: "roles",
    to: "camouflage",
  },
  camouflage: {
    step: "Step 3 · Camouflage",
    title: "Pick a disguise",
    hint: "Real sites in your server's own network come first.",
    from: "camouflage",
    to: "users",
  },
  users: {
    step: "Step 4 · People",
    title: "Name your people",
    hint: "Any language. Everyone gets their own link.",
    from: "users",
    to: "apply",
  },
  apply: {
    step: "Step 5 · Set up",
    title: "Meridian does the rest",
    hint: "Hardening, firewall, certificates, panel, VPN. Then it checks every link.",
    from: "apply",
    to: "handoff",
  },
  handoff: {
    step: "Done",
    title: "Share the links",
    hint: "A connection page and a subscription link for each person.",
    from: "handoff",
    to: "",
  },
};

export function SetupStep({ id }: { id: string }) {
  const step = STEPS[id];
  const from = step.from ? mark(step.from) : 0;
  const to = step.to ? mark(step.to) - BEFORE : setup.duration;
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
  const width = 400;
  const screenHeight = 820;
  const shotHeight = (3822 / 1170) * width; // page-qr.png
  const scroll = interpolate(frame, [60, durationInFrames - 70], [0, shotHeight * 0.42], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  return (
    <>
      <SidePanel step="On her phone" title="One tap to connect" hint="A QR code, an app to install, and one button that adds every server. The app keeps it up to date." />
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
            <Img src={staticFile("phone/page-qr.png")} style={{ width, transform: `translateY(${-scroll}px)` }} />
          </div>
        </div>
      </Rise>
    </>
  );
}

const LATER = [
  ["meridian client add Grandma", "a new person, a new link"],
  ["meridian client remove Alex", "their link stops working"],
  ["meridian check", "looks over your server"],
  ["meridian test", "real traffic through every connection"],
];

export function Later() {
  return (
    <div style={{ position: "absolute", inset: "170px 200px" }}>
      <Rise>
        <Kicker>Later on</Kicker>
      </Rise>
      <Rise delay={4} style={{ marginBottom: 56 }}>
        <Heading size={72}>Everyday commands.</Heading>
      </Rise>
      {LATER.map(([command, what], index) => (
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

function Method({ name, what, delay, blockedAt, activeAt }: { name: string; what: string; delay: number; blockedAt?: number; activeAt?: number }) {
  const frame = useCurrentFrame();
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
        <div style={{ fontFamily: font.heading, fontSize: 42, color: color.text, textDecoration: blocked ? "line-through" : "none" }}>
          {name}
        </div>
        <Body style={{ marginTop: 10, fontSize: 26 }}>{blocked ? "blocked: the app moves on" : active ? "connected" : what}</Body>
      </div>
    </Rise>
  );
}

export function Why() {
  const { durationInFrames } = useVideoConfig();
  const at = (share: number) => Math.round(durationInFrames * share); // follow the narration's sentences
  return (
    <div style={{ position: "absolute", inset: "150px 160px" }}>
      <Rise>
        <Kicker>Why it's hard to block</Kicker>
      </Rise>
      <Rise delay={4}>
        <Heading size={66}>It looks like an ordinary website.</Heading>
      </Rise>
      <Rise delay={20} style={{ marginTop: 30 }}>
        <Body style={{ fontSize: 32 }}>
          Every server offers three methods. Apps switch on their own if one gets blocked.
        </Body>
      </Rise>
      <div style={{ display: "flex", gap: 40, marginTop: 70 }}>
        <Method delay={at(0.4)} name="Reality" what="a visit to the camouflage site" blockedAt={at(0.74)} />
        <Method delay={at(0.4) + 15} name="XHTTP" what="ordinary HTTPS to your server" activeAt={at(0.74) + 20} />
        <Method delay={at(0.4) + 30} name="Hysteria2" what="disguised UDP, fast on poor networks" />
      </div>
    </div>
  );
}

export function Outro() {
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
          <Code style={{ fontSize: 30, color: color.muted }}>uv tool install meridian-vpn &amp;&amp; meridian setup</Code>
        </Rise>
        <Rise delay={30} style={{ marginTop: 80 }}>
          <Body style={{ fontSize: 22, color: color.muted }}>Free and open source · Narration voice by ElevenLabs</Body>
        </Rise>
      </div>
    </div>
  );
}
