import { AbsoluteFill } from "remotion";
import { COPY, type Lang } from "./copy";
import { LangContext } from "./lang";
import { Background, Body, Heading, Kicker } from "./parts";
import { color, font } from "./theme";

/** A still for the README and YouTube: the title card plus a play badge. */
export function Thumbnail({ lang }: { lang: Lang }) {
  const { intro, thumbnail } = COPY[lang];
  const size = lang === "ru" ? 104 : 124; // the Russian line is longer and must clear the play badge
  return (
    <LangContext.Provider value={lang}>
      <AbsoluteFill>
        <Background />
        <div style={{ position: "absolute", left: 150, top: 300 }}>
          <Kicker>{thumbnail.kicker}</Kicker>
          <Heading size={size}>{intro.line1}</Heading>
          <Heading size={size}>
            {intro.before}
            <span style={{ color: color.accent, fontStyle: "italic" }}>{intro.accent}</span>
            {intro.after}
          </Heading>
          <Body style={{ fontSize: 40, color: color.muted, marginTop: 48 }}>
            <span style={{ fontFamily: font.mono, color: color.text }}>meridian setup</span>
            {thumbnail.after}
          </Body>
        </div>
        <div
          style={{
            position: "absolute",
            right: 190,
            top: 440,
            width: 220,
            height: 220,
            borderRadius: 110,
            background: color.accent,
            boxShadow: "0 30px 80px rgba(0,0,0,0.5)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <div
            style={{
              width: 0,
              height: 0,
              marginLeft: 22,
              borderTop: "52px solid transparent",
              borderBottom: "52px solid transparent",
              borderLeft: `84px solid ${color.bg}`,
            }}
          />
        </div>
      </AbsoluteFill>
    </LangContext.Provider>
  );
}
