// Grafik-Vorlagen. Claude wählt pro Szene eine davon aus und füllt sie mit Text.
// Jede Vorlage bekommt:
//   g      – die Daten aus dem Grafik-Plan (z. B. {kind:"counter", value:160000, label:"Sterne"})
//   f      – das aktuelle Bild INNERHALB der Szene (0 = Szenenstart)
//   dur    – Länge der Szene in Bildern
//   t      – das Farb-Thema
//   box    – die Fläche, in der die Grafik liegt ({w, h})

import React from "react";
import { Img, OffthreadVideo, interpolate, spring, staticFile, useVideoConfig, Easing } from "remotion";
import { FONT, MONO, Theme } from "./theme";

export type Box = { w: number; h: number };
export type GraphicProps = { g: any; f: number; dur: number; t: Theme; box: Box };

// ------------------------------------------------------------ kleine Helfer

const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;

/** Federnder Wert 0 → 1, startet bei Bild `delay`. */
const usePop = (f: number, delay = 0, damping = 14) => {
  const { fps } = useVideoConfig();
  return spring({ frame: f - delay, fps, config: { damping, stiffness: 170, mass: 0.7 } });
};

/** Karte mit Thema-Farben. */
const Card: React.FC<{ t: Theme; style?: React.CSSProperties; children: React.ReactNode }> = ({ t, style, children }) => (
  <div
    style={{
      background: t.card,
      border: `3px solid ${t.cardBorder}`,
      borderRadius: 36,
      boxShadow: t.shadow,
      ...style,
    }}
  >
    {children}
  </div>
);

/** Kleine dunkle Pille, z. B. "Step 2". */
const Pill: React.FC<{ t: Theme; children: React.ReactNode; style?: React.CSSProperties }> = ({ t, children, style }) => (
  <div
    style={{
      display: "inline-block",
      background: t.pill,
      color: t.pillText,
      fontFamily: FONT,
      fontWeight: 800,
      fontSize: 36,
      padding: "12px 30px",
      borderRadius: 999,
      letterSpacing: 0.5,
      ...style,
    }}
  >
    {children}
  </div>
);

/** Text, in dem ein Wort (oder mehrere) in der Akzentfarbe steht. */
const Highlighted: React.FC<{ text: string; highlight?: string; t: Theme; f: number; stagger?: number }> = ({
  text, highlight, t, f, stagger = 3,
}) => {
  const hl = (highlight || "").toLowerCase().split(/\s+/).filter(Boolean);
  const words = text.split(/\s+/);
  return (
    <>
      {words.map((w, i) => {
        const p = usePop(f, i * stagger, 16);
        const clean = w.toLowerCase().replace(/[^\p{L}\p{N}]/gu, "");
        const isHl = hl.some((h) => h.replace(/[^\p{L}\p{N}]/gu, "") === clean);
        return (
          <span
            key={i}
            style={{
              display: "inline-block",
              marginRight: "0.25em",
              color: isHl ? t.accent : t.text,
              opacity: p,
              transform: `translateY(${(1 - p) * 40}px) scale(${0.9 + 0.1 * p})`,
            }}
          >
            {w}
          </span>
        );
      })}
    </>
  );
};

// ------------------------------------------------------------ 1. Titel

/** Großer Satz, Wörter ploppen nacheinander rein, Schlüsselwort in Akzentfarbe. */
export const Title: React.FC<GraphicProps> = ({ g, f, t, box }) => {
  const len = (g.text || "").length;
  // Schriftgröße so wählen, dass der Text etwa die halbe Fläche füllt (max. ~3 Zeilen)
  const size = Math.min(140, Math.max(72, Math.sqrt((box.w * 0.86 * Math.min(box.h, 1000) * 0.45) / (Math.max(6, len) * 0.6))));
  return (
    <div style={{ width: box.w * 0.86, textAlign: "center" }}>
      {g.kicker ? (
        <div style={{ marginBottom: 28, opacity: usePop(f, 0) }}>
          <Pill t={t}>{g.kicker}</Pill>
        </div>
      ) : null}
      <div style={{ fontFamily: FONT, fontWeight: 900, fontSize: size, lineHeight: 1.05, letterSpacing: -1.5 }}>
        <Highlighted text={g.text || ""} highlight={g.highlight} t={t} f={f} />
      </div>
      {g.sub ? (
        <div style={{ fontFamily: FONT, fontSize: 48, fontWeight: 600, color: t.muted, marginTop: 26, opacity: usePop(f, 10) }}>
          {g.sub}
        </div>
      ) : null}
    </div>
  );
};

// ------------------------------------------------------------ 2. Zähler

/** Zahl zählt hoch (z. B. 0 → 160.000+), Beschriftung darunter. */
export const Counter: React.FC<GraphicProps> = ({ g, f, t, box }) => {
  const { fps } = useVideoConfig();
  const target = Number(g.value) || 0;
  const progress = interpolate(f, [0, fps * 1.2], [0, 1], { ...clamp, easing: Easing.out(Easing.cubic) });
  const value = Math.round(target * progress);
  const decimals = Number.isInteger(target) ? 0 : 1;
  const shown = (target * progress).toLocaleString("de-DE", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
  const pop = usePop(f);
  const text = `${g.prefix || ""}${decimals ? shown : value.toLocaleString("de-DE")}`;
  const size = Math.min(220, (box.w * 1.5) / Math.max(4, text.length + (g.suffix || "").length));
  return (
    <Card t={t} style={{ padding: "60px 70px", textAlign: "center", transform: `scale(${0.85 + 0.15 * pop})`, opacity: pop, minWidth: box.w * 0.7 }}>
      <div style={{ fontFamily: FONT, fontWeight: 900, fontSize: size, color: t.text, letterSpacing: -3, fontVariantNumeric: "tabular-nums" }}>
        {text}
        <span style={{ color: t.accent }}>{g.suffix || ""}</span>
      </div>
      {g.label ? (
        <div style={{ fontFamily: FONT, fontWeight: 600, fontSize: 52, color: t.muted, marginTop: 8 }}>{g.label}</div>
      ) : null}
    </Card>
  );
};

// ------------------------------------------------------------ 3. Schritt-Karte

/** "Step 2" – Pille + Karte mit Titel. */
export const Step: React.FC<GraphicProps> = ({ g, f, t, box }) => {
  const pop = usePop(f);
  const pop2 = usePop(f, 6);
  return (
    <div style={{ textAlign: "center", width: box.w * 0.8 }}>
      <div style={{ opacity: pop, transform: `translateY(${(1 - pop) * -30}px)` }}>
        <Pill t={t} style={{ fontSize: 42 }}>{g.step || "Step 1"}</Pill>
      </div>
      <Card t={t} style={{ marginTop: 34, padding: "64px 56px", opacity: pop2, transform: `scale(${0.8 + 0.2 * pop2})` }}>
        <div style={{ fontFamily: FONT, fontWeight: 900, fontSize: 100, color: t.text, lineHeight: 1.08 }}>
          <Highlighted text={g.title || ""} highlight={g.highlight} t={t} f={f - 8} />
        </div>
        {g.sub ? <div style={{ fontFamily: FONT, fontSize: 48, color: t.muted, marginTop: 18 }}>{g.sub}</div> : null}
      </Card>
    </div>
  );
};

// ------------------------------------------------------------ 4. Checkliste

const Check: React.FC<{ on: number; t: Theme }> = ({ on, t }) => (
  <div
    style={{
      width: 64, height: 64, borderRadius: 32, flexShrink: 0,
      border: `4px solid ${on > 0.5 ? t.accent2 : t.muted}`,
      background: on > 0.5 ? t.accent2 : "transparent",
      display: "flex", alignItems: "center", justifyContent: "center",
      transform: `scale(${1 + 0.25 * Math.sin(Math.min(1, on) * Math.PI)})`,
    }}
  >
    <svg width="30" height="30" viewBox="0 0 24 24" style={{ opacity: on }}>
      <path d="M4 12.5l5 5L20 6.5" fill="none" stroke="#fff" strokeWidth="3.5" strokeLinecap="round" strokeLinejoin="round"
        strokeDasharray="30" strokeDashoffset={30 * (1 - on)} />
    </svg>
  </div>
);

/** Liste, deren Punkte nacheinander abgehakt werden (über die ganze Szene verteilt). */
export const Checklist: React.FC<GraphicProps> = ({ g, f, dur, t, box }) => {
  const items: string[] = (g.items || []).slice(0, 6);
  const pop = usePop(f);
  const per = Math.max(6, (dur * 0.8) / Math.max(1, items.length));
  return (
    <Card t={t} style={{ width: box.w * 0.86, padding: "50px 56px", opacity: pop, transform: `translateY(${(1 - pop) * 60}px)` }}>
      {g.title ? <div style={{ fontFamily: FONT, fontWeight: 800, fontSize: 62, color: t.text, marginBottom: 24 }}>{g.title}</div> : null}
      {items.map((it, i) => {
        const on = interpolate(f, [8 + i * per, 8 + i * per + 8], [0, 1], clamp);
        const appear = usePop(f, 4 + i * 3);
        return (
          <div key={i} style={{ display: "flex", alignItems: "center", gap: 26, margin: "18px 0", opacity: appear }}>
            <Check on={on} t={t} />
            <div style={{ fontFamily: FONT, fontSize: 56, fontWeight: 600, color: on > 0.5 ? t.text : t.muted }}>{it}</div>
          </div>
        );
      })}
    </Card>
  );
};

// ------------------------------------------------------------ 5. Ordner-Baum

const Folder: React.FC<{ color: string; size: number }> = ({ color, size }) => (
  <svg width={size} height={size * 0.8} viewBox="0 0 100 80">
    <path d="M4 14 Q4 6 12 6 H38 L46 14 H88 Q96 14 96 22 V70 Q96 76 88 76 H12 Q4 76 4 70 Z" fill={color} />
    <path d="M4 26 H96 V70 Q96 76 88 76 H12 Q4 76 4 70 Z" fill={color} style={{ filter: "brightness(1.12)" }} />
  </svg>
);

/** Ordner oben, Linien wachsen nach unten, Unterordner ploppen nacheinander auf. */
export const Tree: React.FC<GraphicProps> = ({ g, f, t, box }) => {
  const kids: string[] = (g.children || []).slice(0, 5);
  const rootPop = usePop(f);
  const line = interpolate(f, [8, 20], [0, 1], clamp);
  const w = box.w * 0.84;
  const step = w / Math.max(1, kids.length);
  return (
    <div style={{ width: w, display: "flex", flexDirection: "column", alignItems: "center" }}>
      <div style={{ transform: `scale(${rootPop})`, textAlign: "center" }}>
        <Folder color={t.folder} size={220} />
        <div style={{ fontFamily: MONO, fontSize: 44, color: t.text, marginTop: 6 }}>{g.root || "Projekt"}</div>
      </div>
      <svg width={w} height={90} style={{ marginTop: 14 }}>
        <line x1={w / 2} y1={0} x2={w / 2} y2={40 * line} stroke={t.muted} strokeWidth={4} />
        <line x1={step / 2 + (w / 2 - step / 2) * (1 - line)} y1={40} x2={w - step / 2 - (w / 2 - step / 2) * (1 - line)} y2={40}
          stroke={t.muted} strokeWidth={4} opacity={line > 0 ? 1 : 0} />
        {kids.map((_, i) => (
          <line key={i} x1={step / 2 + i * step} y1={40} x2={step / 2 + i * step} y2={40 + 50 * interpolate(f, [18, 24], [0, 1], clamp)}
            stroke={t.muted} strokeWidth={4} />
        ))}
      </svg>
      <div style={{ display: "flex", width: w }}>
        {kids.map((k, i) => {
          const p = usePop(f, 22 + i * 6);
          return (
            <div key={i} style={{ width: step, textAlign: "center", transform: `scale(${p})`, opacity: p }}>
              <Folder color={t.folder} size={Math.min(130, step * 0.7)} />
              <div style={{ fontFamily: MONO, fontSize: Math.min(36, step / 7), color: t.text, padding: "0 6px", lineHeight: 1.15 }}>{k}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
};

// ------------------------------------------------------------ 6. Bild / B-Roll

/** Screenshot oder Clip aus dem assets/-Ordner in einer Karte, leichter Zoom (Ken Burns). */
export const ImageCard: React.FC<GraphicProps> = ({ g, f, dur, t, box }) => {
  const pop = usePop(f);
  const zoom = interpolate(f, [0, dur], [1.0, 1.08]);
  const src = g.src ? staticFile(g.src) : null;
  const isVideo = /\.(mp4|mov|m4v|webm)$/i.test(g.src || "");
  const w = box.w * 0.86;
  const h = Math.min(box.h * 0.72, w * 0.62);
  return (
    <div style={{ textAlign: "center", opacity: pop, transform: `translateY(${(1 - pop) * 120}px)` }}>
      <Card t={t} style={{ width: w, height: h, overflow: "hidden", padding: 0 }}>
        {src ? (
          isVideo ? (
            <OffthreadVideo src={src} muted style={{ width: "100%", height: "100%", objectFit: "cover", transform: `scale(${zoom})` }} />
          ) : (
            <Img src={src} style={{ width: "100%", height: "100%", objectFit: "cover", transform: `scale(${zoom})` }} />
          )
        ) : null}
      </Card>
      {g.caption ? (
        <div style={{ marginTop: 26 }}>
          <Pill t={t}>{g.caption}</Pill>
        </div>
      ) : null}
    </div>
  );
};

// ------------------------------------------------------------ 7. Prompt-Box (tippt Text)

/** Eingabefeld wie bei einem Chat, der Text wird Buchstabe für Buchstabe getippt. */
export const Prompt: React.FC<GraphicProps> = ({ g, f, dur, t, box }) => {
  const pop = usePop(f);
  const text: string = g.text || "";
  const typeEnd = Math.min(dur * 0.7, 6 + text.length * 1.2);
  const chars = Math.round(interpolate(f, [6, typeEnd], [0, text.length], clamp));
  const cursor = Math.floor(f / 8) % 2 === 0;
  const sent = interpolate(f, [typeEnd + 4, typeEnd + 10], [0, 1], clamp);
  return (
    <Card t={t} style={{ width: box.w * 0.86, padding: "34px 40px", opacity: pop, transform: `scale(${0.9 + 0.1 * pop})`, textAlign: "left" }}>
      <div style={{ display: "flex", gap: 12, marginBottom: 20 }}>
        {["#FF5F57", "#FEBC2E", "#28C840"].map((c) => <div key={c} style={{ width: 18, height: 18, borderRadius: 9, background: c }} />)}
        <div style={{ fontFamily: FONT, fontSize: 24, color: t.muted, marginLeft: 12, fontWeight: 700, letterSpacing: 2 }}>
          {(g.label || "PROMPT").toUpperCase()}
        </div>
      </div>
      <div style={{ fontFamily: FONT, fontSize: 58, color: t.text, minHeight: 150, lineHeight: 1.3 }}>
        {text.slice(0, chars)}
        <span style={{ opacity: cursor && sent < 1 ? 1 : 0, color: t.accent }}>|</span>
      </div>
      <div style={{ display: "flex", justifyContent: "flex-end" }}>
        <div style={{ width: 64, height: 64, borderRadius: 32, background: t.accent, transform: `scale(${1 + 0.2 * Math.sin(sent * Math.PI)})`,
          display: "flex", alignItems: "center", justifyContent: "center" }}>
          <svg width="30" height="30" viewBox="0 0 24 24"><path d="M12 19V5M5 12l7-7 7 7" stroke="#fff" strokeWidth="3" fill="none" strokeLinecap="round" strokeLinejoin="round" /></svg>
        </div>
      </div>
    </Card>
  );
};

// ------------------------------------------------------------ Auswahl

export const GRAPHICS: Record<string, React.FC<GraphicProps>> = {
  title: Title,
  counter: Counter,
  step: Step,
  checklist: Checklist,
  tree: Tree,
  image: ImageCard,
  prompt: Prompt,
};
