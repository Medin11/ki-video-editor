"""
tools.py – findet die externen Programme (ffmpeg, ffprobe, claude).

Warum? Das normale Homebrew-ffmpeg kann keine Untertitel einbrennen (kein libass).
Deshalb nutzen wir bevorzugt "ffmpeg-full" von Homebrew. Das liegt aber nicht im
normalen PATH ("keg-only"), also suchen wir es hier gezielt.
"""

import shutil
from pathlib import Path

# Orte, an denen ffmpeg-full liegen kann (Apple Silicon bzw. Intel-Mac)
_FFMPEG_FULL_DIRS = [
    Path("/opt/homebrew/opt/ffmpeg-full/bin"),
    Path("/usr/local/opt/ffmpeg-full/bin"),
]


def _find(name):
    """Erst ffmpeg-full probieren, dann das Programm aus dem normalen PATH."""
    for folder in _FFMPEG_FULL_DIRS:
        candidate = folder / name
        if candidate.exists():
            return str(candidate)
    return shutil.which(name)


def ffmpeg_path():
    return _find("ffmpeg")


def ffprobe_path():
    return _find("ffprobe")


def claude_path():
    # claude liegt oft in ~/.local/bin – das fehlt manchmal im PATH,
    # wenn die App per Doppelklick gestartet wird.
    found = shutil.which("claude")
    if found:
        return found
    fallback = Path.home() / ".local" / "bin" / "claude"
    return str(fallback) if fallback.exists() else None


# ---------------------------------------------------------------- Hilfsfunktionen

import json
import subprocess


class UserError(Exception):
    """Fehler mit einer freundlichen Meldung, die direkt in der Oberfläche angezeigt wird."""


def probe(video_path):
    """
    Liest Infos über ein Video aus (Dauer, Breite, Höhe, ob Ton vorhanden ist).
    Wirft UserError, wenn die Datei kein lesbares Video ist.
    """
    cmd = [ffprobe_path(), "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", str(video_path)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise UserError("Diese Datei kann ich leider nicht lesen – ist es wirklich ein Video?")

    info = json.loads(result.stdout)
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    if video is None:
        raise UserError("In dieser Datei ist keine Videospur.")
    has_audio = any(s.get("codec_type") == "audio" for s in info.get("streams", []))

    duration = float(info.get("format", {}).get("duration") or video.get("duration") or 0)

    # Bildrate, z. B. "30000/1001" -> 29.97
    fps = 30.0
    for key in ("avg_frame_rate", "r_frame_rate"):
        num, _, den = (video.get(key) or "0/0").partition("/")
        try:
            if float(den or 1) > 0 and float(num) > 0:
                fps = float(num) / float(den or 1)
                break
        except ValueError:
            pass

    # Hochkant gefilmte Handyvideos sind oft quer gespeichert + "drehen"-Info
    width, height = int(video.get("width", 0)), int(video.get("height", 0))
    rotation = 0
    for side in video.get("side_data_list", []) or []:
        if "rotation" in side:
            rotation = int(side["rotation"])
    if abs(rotation) % 180 == 90:
        width, height = height, width

    return {
        "duration": duration,
        "width": width,
        "height": height,
        "fps": fps,
        "has_audio": has_audio,
        "codec": video.get("codec_name"),
        # HDR (iPhone: "arib-std-b67" = HLG, sonst "smpte2084" = PQ) muss umgerechnet werden
        "hdr": video.get("color_transfer") in ("arib-std-b67", "smpte2084"),
    }


def run_ffmpeg(args, duration=None, on_progress=None):
    """
    Führt ffmpeg mit den gegebenen Argumenten aus.
    Wenn `duration` (Sekunden) und `on_progress` angegeben sind, wird der
    Fortschritt in Prozent (0–100) laufend an on_progress gemeldet.
    """
    cmd = [ffmpeg_path(), "-hide_banner", "-y", "-nostdin",
           "-progress", "pipe:1", "-nostats", *args]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    # stderr in einem eigenen Thread mitlesen, damit der Puffer nicht volläuft
    import threading
    err_lines = []
    t = threading.Thread(target=lambda: err_lines.extend(proc.stderr), daemon=True)
    t.start()

    # ffmpeg schreibt Zeilen wie "out_time_us=12345678" auf stdout
    for line in proc.stdout:
        if on_progress and duration and line.startswith("out_time_us="):
            try:
                seconds = int(line.split("=", 1)[1]) / 1_000_000
                on_progress(max(0, min(100, seconds / duration * 100)))
            except ValueError:
                pass  # z. B. "N/A" am Anfang

    proc.wait()
    t.join(timeout=5)
    if proc.returncode != 0:
        # Details für die Konsole, freundliche Meldung für die Oberfläche
        print("ffmpeg-Fehler:\n" + "".join(err_lines[-30:]))
        raise UserError("Beim Bearbeiten des Videos ist ffmpeg auf einen Fehler gestoßen.")
    return "".join(err_lines)  # manche Filter (z. B. silencedetect) melden Ergebnisse hier
