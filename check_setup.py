#!/usr/bin/env python3
"""
check_setup.py – prüft, ob alles für den KI-Video-Editor installiert ist.

Aufruf:
    python3 check_setup.py            # nur prüfen
    python3 check_setup.py --install  # Fehlendes (ffmpeg, .venv, Pakete) automatisch installieren

Geprüft wird:
  1. Python-Version
  2. ffmpeg + ffprobe
  3. ffmpeg-Filter: libass (für Untertitel) und silencedetect (für Pausen)
  4. Claude-Code-CLI (`claude`)
  5. Whisper in der virtuellen Umgebung (.venv)
"""

import platform
import shutil
import subprocess
import sys
from pathlib import Path

# Gemeinsamer Helfer, der ffmpeg-full / claude findet (auch außerhalb des PATH)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from editor.tools import claude_path, ffmpeg_path, ffprobe_path  # noqa: E402

# Ordner, in dem dieses Skript liegt = Projektordner
PROJECT_DIR = Path(__file__).resolve().parent
VENV_DIR = PROJECT_DIR / ".venv"
VENV_PYTHON = VENV_DIR / "bin" / "python"

# Apple Silicon (M1/M2/…) -> mlx-whisper, Intel -> faster-whisper
IS_APPLE_SILICON = platform.machine() == "arm64"
WHISPER_MODULE = "mlx_whisper" if IS_APPLE_SILICON else "faster_whisper"
WHISPER_PACKAGE = "mlx-whisper" if IS_APPLE_SILICON else "faster-whisper"

GREEN, RED, YELLOW, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[0m"


def ok(msg):
    print(f"  {GREEN}✔{RESET} {msg}")


def fail(msg, hint=None):
    print(f"  {RED}✘{RESET} {msg}")
    if hint:
        print(f"      → {hint}")


def warn(msg):
    print(f"  {YELLOW}!{RESET} {msg}")


def run(cmd):
    """Befehl ausführen und Ausgabe als Text zurückgeben ('' bei Fehler)."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return result.stdout + result.stderr
    except (OSError, subprocess.TimeoutExpired):
        return ""


# ---------------------------------------------------------------- Prüfungen

def check_python():
    v = sys.version_info
    if v >= (3, 9):
        ok(f"Python {v.major}.{v.minor}.{v.micro}")
        return True
    fail(f"Python {v.major}.{v.minor} ist zu alt (mindestens 3.9 nötig)",
         "brew install python@3.12")
    return False


def check_ffmpeg():
    ffmpeg, ffprobe = ffmpeg_path(), ffprobe_path()
    if not ffmpeg or not ffprobe:
        fail("ffmpeg / ffprobe nicht gefunden", "brew install ffmpeg-full")
        return False
    version = run([ffmpeg, "-version"]).splitlines()[0]
    ok(f"{version.split(' Copyright')[0]}  ({ffmpeg})")

    filters = run([ffmpeg, "-hide_banner", "-filters"])
    all_good = True
    if " ass " in filters and " subtitles " in filters:
        ok("ffmpeg kann Untertitel einbrennen (libass)")
    else:
        fail("ffmpeg ohne libass gebaut – Untertitel gehen nicht",
             "brew install ffmpeg-full  (das normale 'ffmpeg' von Homebrew hat kein libass)")
        all_good = False
    if " silencedetect " in filters:
        ok("ffmpeg kann Pausen erkennen (silencedetect)")
    else:
        fail("ffmpeg-Filter silencedetect fehlt", "brew reinstall ffmpeg")
        all_good = False
    return all_good


def check_claude():
    path = claude_path()
    if not path:
        fail("Claude-Code-CLI (`claude`) nicht gefunden",
             "Installieren: https://claude.com/claude-code – danach `claude` einmal starten und einloggen")
        return False
    version = run([path, "--version"]).strip() or "unbekannte Version"
    ok(f"Claude Code gefunden ({version})")
    return True


def check_whisper():
    if not VENV_PYTHON.exists():
        fail("Virtuelle Umgebung .venv fehlt noch",
             "python3 check_setup.py --install")
        return False
    out = run([str(VENV_PYTHON), "-c", f"import {WHISPER_MODULE}; print('OK')"])
    if "OK" in out:
        ok(f"{WHISPER_PACKAGE} ist in .venv installiert")
        return True
    fail(f"{WHISPER_PACKAGE} fehlt in .venv", "python3 check_setup.py --install")
    return False


def check_motion():
    """Optional: Node.js + Remotion für animierte Grafiken (Referenz-Stil)."""
    motion_dir = Path(__file__).resolve().parent / "motion"
    node = next((str(p) for p in (Path("/opt/homebrew/bin/node"), Path("/usr/local/bin/node")) if p.exists()),
                shutil.which("node"))
    if not node:
        warn("Node.js fehlt – animierte Grafiken gehen nicht (Rest funktioniert). Installieren: brew install node")
        return True
    if not (motion_dir / "node_modules" / "@remotion" / "cli").exists():
        warn("Remotion fehlt – im Ordner motion/ einmal `npm install --ignore-scripts` ausführen")
        return True
    ok(f"Node.js {run([node, '--version']).strip()} + Remotion für animierte Grafiken")
    return True


# ---------------------------------------------------------------- Installation

def install_ffmpeg():
    ffmpeg = ffmpeg_path()
    if ffmpeg and " ass " in run([ffmpeg, "-hide_banner", "-filters"]):
        return  # schon da, inklusive libass
    if not shutil.which("brew"):
        fail("Homebrew fehlt – ffmpeg kann nicht automatisch installiert werden",
             "Homebrew installieren: https://brew.sh")
        return
    print("\nInstalliere ffmpeg-full mit Homebrew (kann ein paar Minuten dauern)…")
    subprocess.run(["brew", "install", "ffmpeg-full"])


def install_venv():
    if not VENV_PYTHON.exists():
        print("\nLege virtuelle Umgebung .venv an…")
        subprocess.run([sys.executable, "-m", "venv", str(VENV_DIR)], check=True)
    print("Installiere Python-Pakete aus requirements.txt…")
    subprocess.run([str(VENV_PYTHON), "-m", "pip", "install", "--upgrade", "pip", "-q"])
    subprocess.run([str(VENV_PYTHON), "-m", "pip", "install", "-r",
                    str(PROJECT_DIR / "requirements.txt")])


# ---------------------------------------------------------------- Hauptprogramm

def main():
    if "--install" in sys.argv:
        install_ffmpeg()
        install_venv()

    print("\nKI-Video-Editor – Setup-Check")
    print(f"Mac-Prozessor: {'Apple Silicon' if IS_APPLE_SILICON else 'Intel'}\n")

    results = [
        check_python(),
        check_ffmpeg(),
        check_claude(),
        check_whisper(),
        check_motion(),
    ]

    print()
    if all(results):
        print(f"{GREEN}Alles bereit! 🎬{RESET}")
        return 0
    print(f"{RED}Es fehlt noch etwas (siehe oben).{RESET}")
    if "--install" not in sys.argv:
        print("Tipp: `python3 check_setup.py --install` installiert das meiste automatisch.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
