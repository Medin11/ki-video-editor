// Farb-Themen – abgeleitet aus den drei Referenzvideos in references/.
//   minimal = Video 1 (weiß, schlicht, blaue Ordner)
//   coral   = Video 2 (cremefarben, Karten mit Koralle-Rand)
//   dark    = Video 3 (dunkel, filmisch, rote/türkise Akzente)

export type Theme = {
  bg: string;          // Hintergrund der Grafik-Fläche
  bgGradient: string;  // leichter Verlauf darüber
  card: string;        // Kartenfarbe
  cardBorder: string;  // Kartenrand
  text: string;        // Haupttext
  muted: string;       // Nebentext
  accent: string;      // Akzentfarbe (hervorgehobene Wörter, Zahlen, Häkchen)
  accent2: string;     // zweite Akzentfarbe
  pill: string;        // dunkle Pille hinter Untertiteln / Labels
  pillText: string;
  folder: string;      // Ordner-Symbole
  shadow: string;
};

export const THEMES: Record<string, Theme> = {
  minimal: {
    bg: "#F3F4F6",
    bgGradient: "radial-gradient(120% 80% at 50% 0%, #FFFFFF 0%, #EEF0F3 70%)",
    card: "#FFFFFF",
    cardBorder: "rgba(0,0,0,0.06)",
    text: "#111214",
    muted: "#6B7280",
    accent: "#0A84FF",
    accent2: "#34C759",
    pill: "#16171B",
    pillText: "#FFFFFF",
    folder: "#3FA9F5",
    shadow: "0 30px 60px rgba(17,18,20,0.12)",
  },
  coral: {
    bg: "#F4EEE7",
    bgGradient: "radial-gradient(120% 80% at 50% 0%, #FBF7F2 0%, #F1E9E0 75%)",
    card: "#FFFCF8",
    cardBorder: "#E8735A",
    text: "#2A211E",
    muted: "#9A8A80",
    accent: "#E8735A",
    accent2: "#4CAF7A",
    pill: "#1B1716",
    pillText: "#FFFFFF",
    folder: "#E8735A",
    shadow: "0 26px 50px rgba(120,60,40,0.16)",
  },
  dark: {
    bg: "#0D1018",
    bgGradient: "radial-gradient(120% 80% at 50% 0%, #1A2030 0%, #0B0E15 75%)",
    card: "#151A26",
    cardBorder: "rgba(255,255,255,0.08)",
    text: "#FFFFFF",
    muted: "#8A93A6",
    accent: "#FF3B5C",
    accent2: "#4FD1FF",
    pill: "#0A0C12",
    pillText: "#FFFFFF",
    folder: "#4FD1FF",
    shadow: "0 30px 70px rgba(0,0,0,0.55)",
  },
};

export const FONT =
  '"SF Pro Display", "Helvetica Neue", Helvetica, Arial, sans-serif';
export const MONO = '"SF Mono", Menlo, Monaco, monospace';
