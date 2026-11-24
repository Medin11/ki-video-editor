"""
motion.py – animierte Grafiken, Layouts und Untertitel mit Remotion (Ordner motion/).

Ablauf (wird von render.py aufgerufen, wenn der Plan "motion_graphics" enthält):
  1. render.py hat das Video schon geschnitten (Pausen raus, Zoom, Lautstärke) → cut.mp4
  2. Claude plant die Grafiken anhand der Wörter im GESCHNITTENEN Video (planner.plan_graphics)
  3. Hier: alles in eine props.json schreiben und `remotion render` starten → fertiges 9:16-Video

Remotion braucht Node.js (`brew install node`) und einmalig `npm install` im Ordner motion/.
Eigene Bilder/Clips für B-Roll kommen in den Ordner assets/ (neben app.py).
"""

import json
import re
import shutil
import subprocess
import threading
from pathlib import Path

from .tools import UserError

ROOT = Path(__file__).resolve().parent.parent
MOTION_DIR = ROOT / "motion"
ASSETS_DIR = ROOT / "assets"
REMOTION_CLI = MOTION_DIR / "node_modules" / "@remotion" / "cli" / "remotion-cli.js"

THEMES = ["coral", "minimal", "dark"]
ASSET_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".mov", ".m4v", ".webm"}

# Orte, an denen Node liegen kann (die Mac-App startet ohne den normalen Terminal-PATH)
_NODE_CANDIDATES = [Path("/opt/homebrew/bin/node"), Path("/usr/local/bin/node")]


def node_path():
    for p in _NODE_CANDIDATES:
        if p.exists():
            return str(p)
    return shutil.which("node")


def check_ready():
    """Wirft eine freundliche Fehlermeldung, wenn Node/Remotion fehlen."""
    if not node_path():
        raise UserError("Für animierte Grafiken fehlt Node.js. Bitte im Terminal `brew install node` ausführen.")
    if not REMOTION_CLI.exists():
        raise UserError("Remotion ist nicht installiert. Bitte im Ordner motion/ einmal `npm install` ausführen.")


def list_assets():
    """Dateinamen im assets/-Ordner (Screenshots, B-Roll), die Claude verwenden darf."""
    if not ASSETS_DIR.exists():
        return []
    return sorted(p.name for p in ASSETS_DIR.iterdir()
                  if p.is_file() and p.suffix.lower() in ASSET_EXTENSIONS and not p.name.startswith("."))


def render_motion(cut_path, output_path, info, scenes, caption_groups, theme, work_dir, on_progress=None):
    """
    Rendert das fertige Video mit Remotion.
      cut_path       – das geschnittene Video (wird als Hauptvideo verwendet)
      info           – probe()-Infos des geschnittenen Videos
      scenes         – geprüfter Grafik-Plan [{start, end, layout, graphic?}]
      caption_groups – [{start, end, words:[{text, start, end}]}] oder None
    """
    check_ready()
    work_dir = Path(work_dir)
    public = work_dir / "public"
    public.mkdir(parents=True, exist_ok=True)

    # Remotion liest Dateien aus einem "public"-Ordner: Video + benutzte Assets dorthin
    shutil.copy(cut_path, public / "cut.mp4")
    for s in scenes:
        src = (s.get("graphic") or {}).get("src")
        if src:
            shutil.copy(ASSETS_DIR / src, public / src)

    fps = round(info["fps"]) or 30
    props = {
        "video": "cut.mp4",
        "videoWidth": info["width"],
        "videoHeight": info["height"],
        "fps": fps,
        "durationInFrames": max(1, int(info["duration"] * fps)),
        "theme": theme if theme in THEMES else "coral",
        "captions": caption_groups,
        "scenes": scenes,
    }
    props_file = work_dir / "props.json"
    props_file.write_text(json.dumps(props, ensure_ascii=False), encoding="utf-8")

    cmd = [node_path(), str(REMOTION_CLI), "render", "src/index.ts", "Edit", str(output_path),
           f"--props={props_file}", f"--public-dir={public}",
           "--codec=h264", "--crf=20", "--audio-codec=aac", "--log=info"]
    proc = subprocess.Popen(cmd, cwd=MOTION_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    # Fortschritt: Remotion schreibt Zeilen wie "Rendered 120/900" bzw. "Encoded 120/900"
    log = []
    total = props["durationInFrames"]
    for line in proc.stdout:
        log.append(line)
        m = re.search(r"(Rendered|Encoded|Rendering frames?)\D*(\d+)\s*/\s*(\d+)", line)
        if m and on_progress:
            done, of = int(m.group(2)), int(m.group(3)) or total
            # Rendern = 0–90 %, Encodieren = 90–100 %
            pct = done / of * (90 if m.group(1).startswith("Render") else 100)
            on_progress(max(0.0, min(100.0, pct)))
    proc.wait()
    if proc.returncode != 0 or not Path(output_path).exists():
        print("Remotion-Fehler:\n" + "".join(log[-40:]))
        raise UserError("Beim Rendern der Animationen ist ein Fehler aufgetreten. Details stehen im Log.")


def validate_scenes(raw_scenes, duration):
    """
    Macht den Grafik-Plan von Claude robust:
    sortieren, auf die Videolänge begrenzen, Überlappungen kürzen, Lücken mit "full" füllen,
    unbekannte Grafiken/fehlende Asset-Dateien entfernen.
    """
    from .planner import GRAPHIC_KINDS, LAYOUTS  # hier importiert, um Kreis-Importe zu vermeiden

    assets = set(list_assets())
    scenes = []
    for s in sorted((x for x in raw_scenes if isinstance(x, dict)), key=lambda x: x.get("start", 0)):
        try:
            start = max(0.0, float(s["start"]))
            end = min(duration, float(s["end"]))
        except (KeyError, TypeError, ValueError):
            continue
        if scenes and start < scenes[-1]["end"]:
            start = scenes[-1]["end"]
        if end - start < 0.5:
            continue
        layout = s.get("layout") if s.get("layout") in LAYOUTS else "full"
        graphic = _clean_graphic(s.get("graphic"), assets, GRAPHIC_KINDS)
        if layout == "graphic" and not graphic:
            layout = "full"          # "nur Grafik" ohne Grafik wäre ein leerer Bildschirm
        if layout == "split" and not graphic:
            layout = "full"          # geteilter Bildschirm ohne Grafik hätte oben eine leere Fläche
        scene = {"start": round(start, 3), "end": round(end, 3), "layout": layout}
        if graphic:
            scene["graphic"] = graphic
        scenes.append(scene)

    # Lücken auffüllen (Sprecher bildfüllend)
    filled, cursor = [], 0.0
    for s in scenes:
        if s["start"] - cursor > 0.01:
            filled.append({"start": round(cursor, 3), "end": s["start"], "layout": "full"})
        filled.append(s)
        cursor = s["end"]
    if duration - cursor > 0.01:
        filled.append({"start": round(cursor, 3), "end": round(duration, 3), "layout": "full"})
    return filled


def _clean_graphic(g, assets, kinds):
    if not isinstance(g, dict) or g.get("kind") not in kinds:
        return None
    g = {k: v for k, v in g.items() if v not in (None, "", [])}
    kind = g["kind"]
    if kind == "image" and g.get("src") not in assets:
        print(f"[motion] Bild nicht im assets/-Ordner, Grafik entfernt: {g.get('src')!r}")
        return None
    if kind == "counter":
        value = g.get("value", 0)
        if not isinstance(value, (int, float)):
            try:   # z. B. "160.000" oder "2,5" (deutsche Schreibweise)
                value = float(str(value).replace(".", "").replace(",", "."))
            except ValueError:
                return None
        g["value"] = value
    for key in ("items", "children"):
        if key in g:
            g[key] = [str(x)[:40] for x in g[key] if str(x).strip()][:6]
    for key in ("text", "title"):
        if key in g:
            g[key] = str(g[key])[:90]
    return g


def caption_groups(words, max_words=2, max_chars=16):
    """Wörter (schon mit Zeiten im geschnittenen Video) → kurze Untertitel-Gruppen für Remotion."""
    from .render import _group_words  # gleiche Gruppierung wie bei den ffmpeg-Untertiteln

    groups = _group_words(words, max_words=max_words, max_chars=max_chars)
    out = []
    for gi, group in enumerate(groups):
        end = group[-1]["end"] + 0.3
        if gi + 1 < len(groups):
            end = min(end, groups[gi + 1][0]["start"])
        out.append({
            "start": round(group[0]["start"], 3),
            "end": round(max(end, group[0]["start"] + 0.1), 3),
            "words": [{"text": w["text"].strip().strip(",;:.!?\"„“"), "start": round(w["start"], 3),
                       "end": round(w["end"], 3)} for w in group],
        })
    return out
