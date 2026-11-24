// Die eigentliche Komposition: EIN durchgehendes Video (Ton läuft nie ab), das je nach
// Szene seine Position wechselt (Layouts), dazu Grafiken und animierte Untertitel.
//
// Layouts (wie in den Referenzen):
//   full    – Sprecher bildfüllend
//   split   – Grafik oben, Sprecher unten in abgerundeter Karte   (Referenz 1 + 2)
//   card    – Video als Karte auf unscharfem Hintergrund, Grafik darüber (Referenz 3)
//   graphic – nur Grafik, Sprecher ist weg (Ton läuft weiter)
//
// Die Daten (props) schreibt Python: editor/motion.py

import React from "react";
import { AbsoluteFill, OffthreadVideo, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig, Easing } from "remotion";
import { GRAPHICS } from "./graphics";
import { FONT, THEMES, Theme } from "./theme";

const W = 1080;
const H = 1920;
const TRANSITION = 10; // Bilder für den Wechsel zwischen zwei Layouts

type Word = { text: string; start: number; end: number };
type CaptionGroup = { start: number; end: number; words: Word[] };
type Scene = { start: number; end: number; layout: string; graphic?: any };
export type EditProps = {
  video: string;
  videoWidth: number;
  videoHeight: number;
  fps: number;
  durationInFrames: number;
  theme: string;
  captions: CaptionGroup[] | null;
  scenes: Scene[];
};

type Rect = { x: number; y: number; w: number; h: number; r: number };

// ------------------------------------------------------------ Layout-Geometrie

const SPLIT_SEAM = 820; // Grenze zwischen Grafik (oben) und Sprecher (unten)

function cardRect(vw: number, vh: number): Rect {
  let w = 960;
  let h = (w * vh) / vw;
  if (h > 1000) { h = 1000; w = (h * vw) / vh; }
  return { x: (W - w) / 2, y: 1180 - h / 2, w, h, r: 40 };
}

function videoRect(layout: string, vw: number, vh: number): Rect {
  switch (layout) {
    case "split": return { x: 0, y: SPLIT_SEAM, w: W, h: H - SPLIT_SEAM + 60, r: 56 };
    case "card": return cardRect(vw, vh);
    case "graphic": return { x: 0, y: H + 40, w: W, h: H - SPLIT_SEAM, r: 56 };
    default: return { x: 0, y: 0, w: W, h: H, r: 0 };
  }
}

/** Fläche, in der die Grafik einer Szene liegt. */
function graphicArea(layout: string, vw: number, vh: number): Rect {
  switch (layout) {
    case "split": return { x: 0, y: 0, w: W, h: SPLIT_SEAM, r: 0 };
    case "card": return { x: 0, y: 0, w: W, h: Math.max(420, cardRect(vw, vh).y - 20), r: 0 };
    case "graphic": return { x: 0, y: 0, w: W, h: H, r: 0 };
    default: return { x: 0, y: 140, w: W, h: 700, r: 0 }; // full: Grafik als Einblendung oben
  }
}

/** Wo die Untertitel stehen (Mitte in y). */
function captionY(layout: string, vw: number, vh: number): number {
  switch (layout) {
    case "split": return SPLIT_SEAM;              // genau auf der Nahtstelle (wie Referenz 1)
    case "card": { const c = cardRect(vw, vh); return Math.min(H - 220, c.y + c.h + 90); }
    case "graphic": return 1560;
    default: return 1250;
  }
}

const mix = (a: number, b: number, p: number) => a + (b - a) * p;
const mixRect = (a: Rect, b: Rect, p: number): Rect => ({
  x: mix(a.x, b.x, p), y: mix(a.y, b.y, p), w: mix(a.w, b.w, p), h: mix(a.h, b.h, p), r: mix(a.r, b.r, p),
});

// ------------------------------------------------------------ Komposition

export const Edit: React.FC<EditProps> = (props) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t: Theme = THEMES[props.theme] || THEMES.coral;
  const sec = frame / fps;
  const { videoWidth: vw, videoHeight: vh } = props;

  // Aktuelle und vorherige Szene finden
  const scenes = props.scenes.length ? props.scenes : [{ start: 0, end: 1e9, layout: "full" }];
  let idx = scenes.findIndex((s) => sec >= s.start && sec < s.end);
  if (idx === -1) idx = sec < scenes[0].start ? 0 : scenes.length - 1;
  const scene = scenes[idx];
  const prev = idx > 0 ? scenes[idx - 1] : scene;
  const sceneFrame = frame - Math.round(scene.start * fps);
  const sceneDur = Math.round((scene.end - scene.start) * fps);

  // Übergang zwischen den Layouts (federnd)
  const p = idx === 0 ? 1 : spring({ frame: sceneFrame, fps, config: { damping: 18, stiffness: 140 }, durationInFrames: TRANSITION });
  const rect = mixRect(videoRect(prev.layout, vw, vh), videoRect(scene.layout, vw, vh), p);
  const capY = mix(captionY(prev.layout, vw, vh), captionY(scene.layout, vw, vh), p);
  const blurAmount = mix(prev.layout === "card" ? 1 : 0, scene.layout === "card" ? 1 : 0, p);

  const video = staticFile(props.video);
  const videoStyle: React.CSSProperties = { width: "100%", height: "100%", objectFit: "cover", objectPosition: "50% 35%" };

  return (
    <AbsoluteFill style={{ background: t.bg, fontFamily: FONT }}>
      <AbsoluteFill style={{ background: t.bgGradient }} />

      {/* Unscharfer Hintergrund (nur im Karten-Layout sichtbar) */}
      {blurAmount > 0.01 ? (
        <AbsoluteFill style={{ opacity: blurAmount }}>
          <OffthreadVideo src={video} muted style={{ ...videoStyle, filter: "blur(40px) saturate(0.9)", transform: "scale(1.2)" }} />
          {/* Mit der Themenfarbe einfärben, damit Grafik-Text darauf gut lesbar bleibt */}
          <AbsoluteFill style={{ background: t.bg, opacity: 0.62 }} />
        </AbsoluteFill>
      ) : null}

      {/* Das Hauptvideo – läuft immer durch, damit der Ton nie abreißt */}
      <div
        style={{
          position: "absolute", left: rect.x, top: rect.y, width: rect.w, height: rect.h,
          borderRadius: rect.r, overflow: "hidden",
          boxShadow: rect.r > 1 ? t.shadow : "none",
        }}
      >
        <OffthreadVideo src={video} style={videoStyle} />
      </div>

      {/* Grafik der aktuellen Szene */}
      {scene.graphic ? (
        <GraphicLayer key={idx} scene={scene} sceneFrame={sceneFrame} sceneDur={sceneDur} t={t} vw={vw} vh={vh} />
      ) : null}

      {/* Untertitel */}
      {props.captions ? <Captions groups={props.captions} sec={sec} y={capY} t={t} /> : null}
    </AbsoluteFill>
  );
};

// ------------------------------------------------------------ Grafik-Ebene

const GraphicLayer: React.FC<{ scene: Scene; sceneFrame: number; sceneDur: number; t: Theme; vw: number; vh: number }> = ({
  scene, sceneFrame, sceneDur, t, vw, vh,
}) => {
  const Comp = GRAPHICS[scene.graphic.kind];
  if (!Comp) return null;
  const area = graphicArea(scene.layout, vw, vh);
  // Am Ende der Szene kurz ausblenden
  const out = interpolate(sceneFrame, [sceneDur - 6, sceneDur], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  // Über dem bildfüllenden Sprecher: dunkler Verlauf oben + weißer Text, damit es immer lesbar ist
  const overVideo = scene.layout === "full";
  const theme = overVideo && scene.graphic.kind === "title" ? { ...t, text: "#FFFFFF", muted: "rgba(255,255,255,0.85)" } : t;
  return (
    <>
      {overVideo ? (
        <div style={{ position: "absolute", left: 0, top: 0, width: W, height: 1100, opacity: out,
          background: "linear-gradient(180deg, rgba(0,0,0,0.6) 0%, rgba(0,0,0,0.35) 55%, rgba(0,0,0,0) 100%)" }} />
      ) : null}
      <div
        style={{
          position: "absolute", left: area.x, top: area.y, width: area.w, height: area.h,
          display: "flex", alignItems: "center", justifyContent: "center", opacity: out,
        }}
      >
        <Comp g={scene.graphic} f={sceneFrame} dur={sceneDur} t={theme} box={{ w: area.w, h: area.h }} />
      </div>
    </>
  );
};

// ------------------------------------------------------------ Untertitel

/** 1–2 Wörter, GROSSBUCHSTABEN, dunkle Pille, gesprochenes Wort in Akzentfarbe, plopp beim Erscheinen. */
const Captions: React.FC<{ groups: CaptionGroup[]; sec: number; y: number; t: Theme }> = ({ groups, sec, y, t }) => {
  const { fps } = useVideoConfig();
  const group = groups.find((g) => sec >= g.start && sec < g.end);
  if (!group) return null;
  const local = Math.round((sec - group.start) * fps);
  const pop = spring({ frame: local, fps, config: { damping: 12, stiffness: 220, mass: 0.6 } });
  return (
    <div
      style={{
        position: "absolute", left: 0, width: W, top: y, display: "flex", justifyContent: "center",
        transform: `translateY(-50%) scale(${interpolate(pop, [0, 1], [0.7, 1])})`,
      }}
    >
      <div
        style={{
          background: t.pill, borderRadius: 18, padding: "14px 28px",
          fontFamily: FONT, fontWeight: 900, fontSize: 70, letterSpacing: 0.5, lineHeight: 1.1,
          color: t.pillText, textTransform: "uppercase", whiteSpace: "nowrap",
          boxShadow: "0 10px 30px rgba(0,0,0,0.25)",
        }}
      >
        {group.words.map((w, i) => {
          const active = group.words.length > 1 && sec >= w.start && (i === group.words.length - 1 || sec < group.words[i + 1].start);
          return (
            <span key={i} style={{ color: active ? t.accent : t.pillText }}>
              {i > 0 ? " " : ""}
              {w.text}
            </span>
          );
        })}
      </div>
    </div>
  );
};
