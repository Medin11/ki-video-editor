# KI-Video-Editor – Projektbeschreibung

Eine einfache, lokale Video-Editing-App für meinen Mac. Ich ziehe ein Video rein, tippe unten in eine Textzeile, was passieren soll (z. B. „entferne alle Pausen", „füge Untertitel hinzu", „mach es 9:16 für TikTok"), und die App schneidet das Video automatisch.

Alles läuft lokal auf meinem Mac. Videos werden nirgendwo hochgeladen. Kein API-Key: Claude wird über die Claude-Code-CLI (`claude -p`) mit meinem Claude-Abo aufgerufen.

## Tech-Stack

- **Backend:** Python 3 + Flask (`app.py`), läuft nur auf `127.0.0.1`
- **Frontend:** eine einzige `index.html` mit Vanilla JS + CSS (kein Framework, kein Build-Schritt)
- **Transkription:** `mlx-whisper` (Apple Silicon), Fallback `faster-whisper` (Intel), mit Wort-Zeitstempeln, Sprache automatisch erkennen
- **„Gehirn":** `claude -p` (Claude Code im Headless-Modus) → liefert einen JSON-Schnittplan
- **Schnitt & Effekte:** ffmpeg (über `subprocess`)
- Abhängigkeiten in `requirements.txt`, virtuelle Umgebung in `.venv`

## Oberfläche (so simpel wie möglich)

- Dunkles, cleanes Design, ein Bildschirm
- **Leerer Zustand:** großes Drag-&-Drop-Feld „Video hier reinziehen" (alternativ Datei-Auswahl)
- **Oben/Mitte:** großer Videoplayer mit der aktuellen Version
- **Unten:** eine Textzeile wie ein Chat-Eingabefeld + Senden-Button (Enter schickt ab)
- **Kleine Buttons:** „Rückgängig" und „Exportieren"
- **Status-Anzeige** während der Verarbeitung (z. B. „Transkribiere…", „Claude plant Schnitte…", „Rendere…") mit Ladeanimation
- Kurze Antwort von Claude unter dem Player anzeigen (z. B. „12 Pausen entfernt, Video jetzt 3:41 statt 4:55")
- Kleiner Verlauf der bisherigen Prompts/Versionen

## Funktionen Version 1

1. **Pausen entfernen:** Stille-Stellen rausschneiden (Kombination aus Transkript-Lücken und ffmpeg `silencedetect`; Standard: Pausen > 0,6 s, ca. 0,15 s Puffer behalten, damit es natürlich klingt)
2. **Füllwörter entfernen:** „ähm", „äh", „uh", „um" usw. anhand der Wort-Zeitstempel
3. **Highlights / Kürzen:** z. B. „mach ein 60-Sekunden-Highlight" → Claude wählt die besten Abschnitte aus dem Transkript
4. **Auto-Untertitel:** aus den Wort-Zeitstempeln eine `.ass`-Datei erzeugen und einbrennen; Stil „tiktok" (groß, fett, weiß mit schwarzem Rand, wenige Wörter pro Zeile, unten mittig) und „klassisch"
5. **Format ändern:** 9:16 (1080×1920, mittig zuschneiden), 1:1, 16:9
6. **Geschwindigkeit:** z. B. 1,25× (Video + Audio, `setpts` + `atempo`)
7. **Ein-/Ausblenden:** Fade am Anfang/Ende

## Referenz-Stil (references/)

Im Ordner `references/` liegen 3 Vorlage-Videos (TikTok, 9:16). Daraus abgeleitet (Preset `REFERENCE_STYLE` in `editor/planner.py`, Chip „Schneide im Referenz-Stil"):

- Sehr enge Jump-Cuts: Pausen > 0,3 s raus, nur 0,06 s Puffer (`remove_pauses.buffer`) + Füllwörter raus
- `punch_zoom` (1,12×): wechselt an Schnitten zwischen normal und gezoomt, frühestens alle 2,5 s
- 9:16; optional `aspect.fit = "blur"` (ganzes Bild auf unscharfem Hintergrund)
- Untertitel-Stil `referenz`: 1–2 Wörter, GROSSBUCHSTABEN, Arial Black, weiß auf dunkler Box, gesprochenes Wort in Koralle (#E8735A), kurzer „Plopp"
- `loudness` auf -14 LUFS, keine Blende
- `motion_graphics {theme, captions, notes}`: animierte Grafiken, Layouts und Untertitel mit **Remotion** (siehe unten). Ersetzt `aspect` + `subtitles`.

## Animierte Grafiken (Remotion)

- Ordner `motion/` = Remotion-Projekt (React). Versionen fest gepinnt (remotion 4.0.540, react 19.2.9), installiert mit `npm install --ignore-scripts`. Braucht Node.js (`brew install node`).
- Ablauf in `render.py`: ffmpeg schneidet (Pausen, Füllwörter, Zoom, Lautstärke) → `cut.mp4` → `planner.plan_graphics()` (zweiter `claude -p`-Aufruf, „Motion-Designer") plant Szenen anhand der Wörter im geschnittenen Video → `motion.validate_scenes()` macht den Plan robust → `motion.render_motion()` ruft `remotion render` auf.
- Layouts (`motion/src/Edit.tsx`): `full` (Sprecher bildfüllend), `split` (Grafik oben, Sprecher unten in Karte), `card` (Video als Karte auf unscharfem Hintergrund), `graphic` (nur Grafik). Das Video läuft durchgehend, nur seine Position wird animiert.
- Grafiken (`motion/src/graphics.tsx`): `title`, `counter`, `step`, `checklist`, `tree`, `image` (Dateien aus `assets/`), `prompt` (getippter Text).
- Themen (`motion/src/theme.ts`): `coral` (Referenz 2, Standard), `minimal` (Referenz 1), `dark` (Referenz 3).
- Eigene Screenshots/B-Roll in `assets/` legen; Claude nutzt sie nur, wenn sie zum Gesagten passen.
- Vorschau zum Basteln an den Vorlagen: im Ordner `motion/` `npm run studio`.

Später (noch nicht bauen, aber Architektur offen halten): Hintergrundmusik mit Ducking, Zoom-Ins, Text-/Logo-Overlays, Übergänge, Farbfilter.

## Ablauf eines Prompts

1. Video der aktuellen Version transkribieren (Ergebnis cachen, per Datei-Hash – nicht doppelt transkribieren)
2. `claude -p` aufrufen mit: Systemanweisung + JSON-Schema + Videodauer + Transkript (mit Wort-Zeitstempeln) + Nutzer-Prompt. Transkript über stdin übergeben, `--output-format json` nutzen und das Feld `result` auslesen
3. Antwort als JSON parsen und validieren. Bei ungültigem JSON einmal erneut fragen mit Fehlermeldung; sonst freundliche Fehlermeldung in der UI
4. Schnittplan in ffmpeg-Befehle übersetzen und als neue Version rendern
5. Neue Version im Player anzeigen

Wichtig: Jede Bearbeitung baut auf der **aktuellen** Version auf. Da sich Zeitstempel nach einem Schnitt ändern, wird jede neue Version vor dem nächsten Prompt neu transkribiert (gecacht).

## JSON-Schnittplan (Claude muss exakt dieses Format liefern)

```json
{
  "reply": "Kurze Antwort für den Nutzer auf Deutsch oder in der Sprache des Prompts",
  "operations": [
    { "type": "remove_pauses", "min_pause": 0.6 },
    { "type": "remove_fillers" },
    { "type": "keep_segments", "segments": [ { "start": 0.0, "end": 12.4 } ] },
    { "type": "subtitles", "style": "tiktok" },
    { "type": "aspect", "ratio": "9:16" },
    { "type": "speed", "factor": 1.25 },
    { "type": "fade", "in": 0.5, "out": 0.5 }
  ]
}
```

- Nur Operationen aus dieser Liste erlauben; unbekannte ignorieren und loggen
- Wenn der Wunsch nicht machbar ist: `operations` leer lassen und in `reply` erklären, warum
- Reihenfolge beim Rendern: erst Schnitte (keep_segments / remove_pauses / remove_fillers), dann speed, dann aspect, dann subtitles, dann fade
- Zeitangaben in Sekunden (float)

## ffmpeg-Hinweise

- Präzise Schnitte: neu encodieren (`libx264`, `-preset veryfast`, `-crf 20`, Audio `aac`), nicht `-c copy`
- Mehrere Segmente: `trim`/`atrim` + `concat`-Filter oder Segment-Dateien + concat demuxer
- Untertitel einbrennen mit `subtitles=`/`ass=`-Filter (prüfen, ob ffmpeg mit libass gebaut ist)
- Fortschritt über `-progress pipe:1` auslesen und als Prozent in der UI zeigen
- Lange Videos (1 h+) müssen funktionieren: Verarbeitung in einem Hintergrund-Thread, Frontend fragt Status per Polling ab

## Projekt- und Dateistruktur

```
video-editor/
├── CLAUDE.md
├── app.py              # Flask-Server + API
├── editor/
│   ├── transcribe.py   # Whisper + Cache
│   ├── planner.py      # claude -p Aufruf, Prompt, JSON-Validierung
│   ├── render.py       # JSON-Plan → ffmpeg
│   └── versions.py     # Versionen, Undo, Verlauf
├── static/index.html   # komplette Oberfläche
├── projects/           # pro Video ein Ordner (nicht ins Git)
│   └── <id>/original.mp4, v1.mp4, v2.mp4, transcripts/, history.json
├── requirements.txt
├── check_setup.py      # prüft ffmpeg, python, claude, whisper
└── Start Video Editor.command   # Doppelklick-Starter
```

## API-Endpunkte

- `POST /upload` – Video hochladen (lokal), neues Projekt anlegen
- `POST /prompt` – Prompt schicken, startet Hintergrund-Job, gibt `job_id` zurück
- `GET /status/<job_id>` – Status, Fortschritt, Antworttext
- `POST /undo` – eine Version zurück
- `GET /video/<project>/<version>` – Video für den Player (mit Range-Requests, damit Spulen geht)
- `GET /export/<project>` – aktuelle Version herunterladen bzw. in `~/Movies/Video Editor Exports/` speichern

## Starter (`Start Video Editor.command`)

- Wechselt in den Projektordner
- Legt beim ersten Start `.venv` an und installiert `requirements.txt`
- Startet `app.py` und öffnet automatisch `http://127.0.0.1:5050` im Browser (Port 5000 ist auf dem Mac vom AirPlay-Empfänger belegt)
- Ausführbar machen (`chmod +x`)

## Bau-Reihenfolge

1. `check_setup.py`: prüfen, was installiert ist (ffmpeg inkl. libass, Python, `claude`, Whisper); Fehlendes installieren bzw. mir sagen, was fehlt
2. Grundgerüst: Flask + `index.html` mit Upload, Player, Textzeile (noch ohne KI)
3. Transkription mit Cache
4. `claude -p`-Anbindung + JSON-Schnittplan
5. Render-Engine mit den Funktionen aus Version 1
6. Versionen, Undo, Export
7. Starter-Datei
8. Testen mit einem kurzen und einem langen Video, Fehler beheben

Nach jedem Schritt kurz testen und mir sagen, was ich ausprobieren soll.

## Qualitätsregeln

- Originalvideo niemals überschreiben
- Klare, freundliche Fehlermeldungen in der UI (kein Python-Traceback)
- Code einfach und gut kommentiert halten – ich bin kein Profi-Entwickler
- Server nur lokal (`127.0.0.1`), nichts ins Internet
