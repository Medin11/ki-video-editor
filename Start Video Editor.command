#!/bin/zsh
# ------------------------------------------------------------------
#  Start Video Editor – per Doppelklick im Finder starten.
#
#  Beim ersten Start: legt die Python-Umgebung (.venv) an, installiert
#  alles Nötige und prüft das Setup. Danach startet der Editor und
#  öffnet sich im Browser.
#
#  Beenden: dieses Terminal-Fenster schließen (oder Ctrl+C).
# ------------------------------------------------------------------

# In den Ordner wechseln, in dem diese Datei liegt
cd "$(dirname "$0")" || exit 1

# Beim Doppelklick fehlen manchmal Pfade – Homebrew und Claude Code ergänzen
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"

clear
echo "🎬 KI-Video-Editor"
echo

# Fenster bei einem Fehler offen lassen, damit man die Meldung lesen kann
fail() {
  echo
  echo "❌ $1"
  echo
  read -k 1 "?Taste drücken zum Schließen…"
  exit 1
}

command -v python3 >/dev/null || fail "Python 3 fehlt. Installieren mit:  brew install python"

# 1. Erster Start (oder requirements.txt geändert): Umgebung einrichten
if [[ ! -x .venv/bin/python || requirements.txt -nt .venv/.installed ]]; then
  echo "Erster Start – richte alles ein (dauert ein paar Minuten)…"
  echo
  python3 check_setup.py --install || fail "Einrichtung unvollständig – siehe Meldungen oben."
  touch .venv/.installed
  echo
else
  # Schneller Check bei jedem Start (ohne Installation)
  python3 check_setup.py >/dev/null 2>&1 || {
    python3 check_setup.py
    fail "Es fehlt etwas – siehe oben. Tipp: im Terminal  python3 check_setup.py --install  ausführen."
  }
fi

# 2. Editor starten (öffnet den Browser automatisch)
exec .venv/bin/python app.py
