"""
planner.py – das "Gehirn": fragt Claude (über `claude -p`), was mit dem Video passieren soll.

Claude bekommt: Anweisungen + Videodauer + Transkript + den Wunsch des Nutzers.
Claude liefert: einen JSON-Schnittplan, z. B.
    {"reply": "Ich entferne alle Pausen.", "operations": [{"type": "remove_pauses", "min_pause": 0.6}]}

Kein API-Key nötig: `claude -p` nutzt dein Claude-Abo (Claude Code muss eingeloggt sein).

Testen von der Kommandozeile:
    .venv/bin/python -m editor.planner pfad/zum/video.mp4 "Entferne alle Pausen"
"""

import json
import subprocess
import tempfile

from .tools import UserError, claude_path

# ---------------------------------------------------------------- Einstellungen

# "sonnet" ist schnell und gut. "opus" wählt Highlights evtl. noch besser aus, ist aber langsamer.
MODEL = "sonnet"

TIMEOUT_SECONDS = 300

# Bis zu so vielen Wörtern bekommt Claude jedes Wort mit Zeitstempel (≈ 25 Minuten Sprache).
# Bei längeren Videos nur die Sätze mit Start/Ende – sonst wird der Text zu lang.
MAX_WORDS_WITH_TIMESTAMPS = 4000

ASPECT_RATIOS = ["9:16", "1:1", "16:9"]
SUBTITLE_STYLES = ["referenz", "tiktok", "klassisch"]
ASPECT_FITS = ["crop", "blur"]

# Reihenfolge beim Rendern (siehe CLAUDE.md)
OPERATION_ORDER = ["keep_segments", "remove_pauses", "remove_fillers", "punch_zoom",
                   "speed", "aspect", "subtitles", "loudness", "fade", "motion_graphics"]

# Animierte Grafiken (Remotion, siehe editor/motion.py und motion/src/)
THEMES = ["coral", "minimal", "dark"]
LAYOUTS = ["full", "split", "card", "graphic"]
GRAPHIC_KINDS = ["title", "counter", "step", "checklist", "tree", "image", "prompt"]

# Der "Referenz-Stil" – abgeleitet aus den 3 Videos im Ordner references/:
# enge Jump-Cuts, keine Füllwörter, 9:16, Punch-Zooms zwischen Einstellungen,
# 1–2-Wort-Untertitel in GROSSBUCHSTABEN, laut wie Social Media (-14 LUFS), keine Blende.
REFERENCE_STYLE = [
    {"type": "remove_pauses", "min_pause": 0.3, "buffer": 0.06},
    {"type": "remove_fillers"},
    {"type": "punch_zoom", "amount": 1.12},
    {"type": "loudness", "target": -14},
    {"type": "motion_graphics", "theme": "coral", "captions": True, "notes": ""},
]

# ---------------------------------------------------------------- JSON-Schema
# Claude Code prüft die Antwort schon gegen dieses Schema (--json-schema).
# Zusätzlich prüfen wir unten selbst nochmal (Wertebereiche, Zeiten im Video usw.).

def _op(type_name, props=None, required=()):
    return {
        "type": "object",
        "properties": {"type": {"const": type_name}, **(props or {})},
        "required": ["type", *required],
        "additionalProperties": False,
    }

SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "operations": {
            "type": "array",
            "items": {"anyOf": [
                _op("remove_pauses", {"min_pause": {"type": "number"}, "buffer": {"type": "number"}}),
                _op("punch_zoom", {"amount": {"type": "number"}}),
                _op("loudness", {"target": {"type": "number"}}),
                _op("motion_graphics", {"theme": {"enum": THEMES}, "captions": {"type": "boolean"},
                                        "notes": {"type": "string"}}),
                _op("remove_fillers"),
                _op("keep_segments", {"segments": {"type": "array", "items": {
                    "type": "object",
                    "properties": {"start": {"type": "number"}, "end": {"type": "number"}},
                    "required": ["start", "end"], "additionalProperties": False}}}, ["segments"]),
                _op("subtitles", {"style": {"enum": SUBTITLE_STYLES}}),
                _op("aspect", {"ratio": {"enum": ASPECT_RATIOS}, "fit": {"enum": ASPECT_FITS}}, ["ratio"]),
                _op("speed", {"factor": {"type": "number"}}, ["factor"]),
                _op("fade", {"in": {"type": "number"}, "out": {"type": "number"}}),
            ]},
        },
    },
    "required": ["reply", "operations"],
    "additionalProperties": False,
}

# ---------------------------------------------------------------- Anweisungen für Claude

SYSTEM_PROMPT = """\
Du bist das "Gehirn" eines Video-Editors. Der Nutzer beschreibt in einem Satz, was mit seinem Video \
passieren soll. Du bekommst das Transkript des AKTUELLEN Videos (Zeiten in Sekunden) und lieferst \
einen Schnittplan als JSON. Du schneidest nicht selbst – das Programm führt deinen Plan aus.

Verfügbare Operationen (jede höchstens einmal pro Plan):
- remove_pauses {min_pause, buffer}: entfernt Stille länger als min_pause Sekunden (Standard 0.6). \
buffer = wie viel Stille am Rand stehen bleibt (Standard 0.15; 0.05 = sehr enge Jump-Cuts). \
Das Programm erkennt die Pausen selbst.
- remove_fillers: entfernt Füllwörter wie "ähm", "äh", "hm", "uh", "um". Das Programm findet sie selbst.
- keep_segments {segments:[{start,end}]}: behält NUR diese Abschnitte (in dieser Reihenfolge), alles \
andere fliegt raus. Für: Highlights, Kürzen, bestimmte Stellen/Themen entfernen, Anfang/Ende abschneiden. \
Schneide an Wort- bzw. Satzgrenzen (nutze die Zeitstempel), nimm vollständige Sätze, sortiert, ohne \
Überlappung, innerhalb der Videodauer. Bei einer Zieldauer (z. B. "60 Sekunden") soll die Summe der \
Abschnitte nah daran liegen (±10 %). Für Highlights: starker Einstieg, die interessantesten und \
verständlichen Aussagen, sinnvolles Ende.
- subtitles {style}: brennt Untertitel ein. "referenz" = Stil der Referenzvideos (1–2 Wörter, \
GROSSBUCHSTABEN, weiß auf dunkler Box, gesprochenes Wort in Koralle); "tiktok" = groß, fett, wenige \
Wörter, gelb hervorgehoben; "klassisch" = dezent, ganze Sätze unten.
- aspect {ratio, fit}: "9:16" (TikTok/Reels/Shorts), "1:1" (Quadrat), "16:9" (YouTube/quer). \
fit "crop" (Standard) = mittig zuschneiden; "blur" = ganzes Bild zeigen, Rand mit unscharfer Kopie füllen \
(gut, wenn wichtige Inhalte am Rand eines Querformat-Videos sind).
- punch_zoom {amount}: wechselt an Schnitten zwischen normal und leicht hineingezoomt (amount 1.05–1.3, \
Standard 1.12) – typischer Jump-Cut-Look. Wirkt nur, wenn auch geschnitten wird.
- loudness {target}: Lautstärke angleichen in LUFS (Standard -14 = Social Media).
- motion_graphics {theme, captions, notes}: der komplette Referenz-Look mit animierten Grafiken: \
9:16, wechselnde Layouts (Grafik oben / Sprecher unten, Video als Karte auf unscharfem Hintergrund, \
Vollbild-Grafiken), animierte Grafiken (Titel, Zähler, Schritt-Karten, Checklisten, Ordner-Bäume, \
Bilder/B-Roll aus dem assets-Ordner, getippte Prompts) und animierte Untertitel. theme: "coral" \
(warm, cremefarben – Standard), "minimal" (weiß, schlicht), "dark" (dunkel, filmisch). captions = \
Untertitel ja/nein (Standard true). notes = Wünsche des Nutzers zu den Grafiken (z. B. "zeig bei \
Schritt 2 den Screenshot setup.png"), sonst leer. Welche Grafik wann kommt, plant danach ein \
eigener Schritt – du musst das nicht tun. Mit motion_graphics KEIN aspect und KEINE subtitles \
dazunehmen (macht motion_graphics selbst). Für Videos über 10 Minuten nur auf ausdrücklichen Wunsch \
(Rendern dauert dann lange).
- speed {factor}: Tempo, 0.5 bis 2.0 (z. B. 1.25).
- fade {in, out}: Ein-/Ausblenden in Sekunden (Standard je 0.5; 0 = nicht).

REFERENZ-STIL: Sagt der Nutzer z. B. "im Referenz-Stil", "in meinem Stil", "wie die Referenzen" oder \
"wie meine Vorlagen", dann nimm genau diese Operationen (sofern nicht schon angewendet; passe sie an, \
wenn der Nutzer etwas anderes dazu sagt): {REFERENZ_PLAN}

Regeln:
- "reply": 1–2 kurze, freundliche Sätze in der Sprache des Nutzer-Wunsches, die sagen, was du machst. \
Nenne keine Ergebnis-Zahlen (z. B. neue Länge) – die berechnet das Programm danach selbst.
- Ein Wunsch kann mehrere Operationen brauchen (z. B. "mach es fertig für TikTok" = aspect 9:16 + \
subtitles tiktok, evtl. remove_pauses).
- Was unter "Bereits angewendet" steht, steckt schon im aktuellen Video. Wiederhole das nicht \
(z. B. keine zweiten Untertitel, kein zweites Zuschneiden auf dasselbe Format).
- Ist der Wunsch mit diesen Operationen nicht machbar (z. B. Musik, Logo, Farbfilter, Übergänge), \
oder ist es nur eine Frage zum Video, dann: operations = [] und in reply erklären bzw. antworten.
- Ist das Video stumm bzw. ohne Sprache, gehen Pausen/Füllwörter/Untertitel/Highlights nicht – erkläre das.
- Antworte ausschließlich mit dem JSON-Objekt.
"""
SYSTEM_PROMPT = SYSTEM_PROMPT.replace("{REFERENZ_PLAN}", json.dumps(REFERENCE_STYLE, ensure_ascii=False))


# ---------------------------------------------------------------- Öffentliche Funktionen

def plan(user_prompt, transcript, video_info, applied_operations=None):
    """
    Fragt Claude nach einem Schnittplan und gibt ihn geprüft zurück:
        {"reply": str, "operations": [...]}  (Operationen in Render-Reihenfolge)
    Bei ungültiger Antwort wird Claude einmal mit der Fehlermeldung erneut gefragt.
    """
    context = _build_input(user_prompt, transcript, video_info, applied_operations or [])
    duration = video_info["duration"]

    raw = _ask_claude(context)
    try:
        return validate(raw, duration)
    except ValueError as first_error:
        print(f"[planner] Ungültiger Plan, frage nochmal: {first_error}")
        retry_context = (context + "\n\n---\nDein vorheriger Plan war ungültig:\n"
                         f"{json.dumps(raw, ensure_ascii=False)}\nFehler: {first_error}\n"
                         "Bitte liefere einen korrigierten Plan.")
        raw = _ask_claude(retry_context)
        try:
            return validate(raw, duration)
        except ValueError as second_error:
            print(f"[planner] Auch zweiter Plan ungültig: {second_error}")
            raise UserError("Claude hat keinen brauchbaren Schnittplan geliefert. "
                            "Versuch es bitte nochmal, vielleicht etwas anders formuliert.")


def validate(raw, duration):
    """
    Prüft den Plan auf sinnvolle Werte. Wirft ValueError mit einer Beschreibung des Problems.
    Unbekannte Operationen werden ignoriert (und in der Konsole notiert).
    """
    if not isinstance(raw, dict):
        raise ValueError("Antwort ist kein JSON-Objekt.")
    reply = raw.get("reply")
    if not isinstance(reply, str) or not reply.strip():
        raise ValueError("'reply' fehlt oder ist leer.")
    ops = raw.get("operations")
    if not isinstance(ops, list):
        raise ValueError("'operations' muss eine Liste sein.")

    clean = {}
    for op in ops:
        kind = op.get("type") if isinstance(op, dict) else None
        if kind not in OPERATION_ORDER:
            print(f"[planner] Unbekannte Operation ignoriert: {op!r}")
            continue
        if kind in clean:
            raise ValueError(f"Operation '{kind}' kommt mehrfach vor.")
        clean[kind] = _validate_op(kind, op, duration)

    if "motion_graphics" in clean:
        # Format (9:16) und Untertitel übernimmt Remotion – sonst gäbe es sie doppelt
        clean.pop("aspect", None)
        clean.pop("subtitles", None)

    operations = [clean[k] for k in OPERATION_ORDER if k in clean]
    return {"reply": reply.strip(), "operations": operations}


def describe(operations):
    """Kurze, menschenlesbare Beschreibung der Operationen (für die Oberfläche)."""
    parts = []
    for op in operations:
        t = op["type"]
        if t == "remove_pauses":
            parts.append(f"Pausen > {op['min_pause']:g} s entfernen")
        elif t == "punch_zoom":
            parts.append(f"Punch-Zoom {op['amount']:g}×")
        elif t == "loudness":
            parts.append(f"Lautstärke {op['target']:g} LUFS")
        elif t == "motion_graphics":
            parts.append(f"Animierte Grafiken ({op['theme']})")
        elif t == "remove_fillers":
            parts.append("Füllwörter entfernen")
        elif t == "keep_segments":
            total = sum(s["end"] - s["start"] for s in op["segments"])
            parts.append(f"{len(op['segments'])} Abschnitt(e) behalten ({total:.0f} s)")
        elif t == "subtitles":
            parts.append(f"Untertitel ({op['style']})")
        elif t == "aspect":
            parts.append(f"Format {op['ratio']}" + (" (unscharfer Rand)" if op.get("fit") == "blur" else ""))
        elif t == "speed":
            parts.append(f"Tempo {op['factor']:g}×")
        elif t == "fade":
            parts.append(f"Blende ({op['in']:g} s / {op['out']:g} s)")
    return ", ".join(parts)


# ---------------------------------------------------------------- Prüfung einzelner Operationen

def _number(op, key, default, low, high):
    value = op.get(key, default)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"'{key}' bei {op['type']} muss eine Zahl sein.")
    if not low <= value <= high:
        raise ValueError(f"'{key}' bei {op['type']} muss zwischen {low} und {high} liegen (war {value}).")
    return float(value)


def _validate_op(kind, op, duration):
    if kind == "remove_pauses":
        return {"type": kind, "min_pause": _number(op, "min_pause", 0.6, 0.2, 10),
                "buffer": _number(op, "buffer", 0.15, 0.0, 0.5)}
    if kind == "punch_zoom":
        return {"type": kind, "amount": _number(op, "amount", 1.12, 1.05, 1.3)}
    if kind == "loudness":
        return {"type": kind, "target": _number(op, "target", -14, -30, -9)}
    if kind == "motion_graphics":
        theme = op.get("theme", "coral")
        if theme not in THEMES:
            raise ValueError(f"theme muss einer von {THEMES} sein.")
        return {"type": kind, "theme": theme, "captions": op.get("captions", True) is not False,
                "notes": str(op.get("notes") or "")[:500]}
    if kind == "remove_fillers":
        return {"type": kind}
    if kind == "subtitles":
        style = op.get("style", "tiktok")
        if style not in SUBTITLE_STYLES:
            raise ValueError(f"Untertitel-Stil muss einer von {SUBTITLE_STYLES} sein.")
        return {"type": kind, "style": style}
    if kind == "aspect":
        if op.get("ratio") not in ASPECT_RATIOS:
            raise ValueError(f"Format muss eines von {ASPECT_RATIOS} sein.")
        fit = op.get("fit", "crop")
        if fit not in ASPECT_FITS:
            raise ValueError(f"fit muss einer von {ASPECT_FITS} sein.")
        return {"type": kind, "ratio": op["ratio"], "fit": fit}
    if kind == "speed":
        return {"type": kind, "factor": _number(op, "factor", 1.0, 0.5, 2.0)}
    if kind == "fade":
        fade_in = _number(op, "in", 0.5, 0, 10)
        fade_out = _number(op, "out", 0.5, 0, 10)
        if fade_in + fade_out > duration:
            raise ValueError("Ein- und Ausblenden zusammen sind länger als das Video.")
        return {"type": kind, "in": fade_in, "out": fade_out}
    if kind == "keep_segments":
        return {"type": kind, "segments": _validate_segments(op.get("segments"), duration)}
    raise ValueError(f"Unbekannte Operation {kind}")


def _validate_segments(segments, duration):
    if not isinstance(segments, list) or not segments:
        raise ValueError("keep_segments braucht mindestens einen Abschnitt.")
    clean = []
    for s in segments:
        if not isinstance(s, dict):
            raise ValueError("Jeder Abschnitt braucht 'start' und 'end'.")
        start, end = s.get("start"), s.get("end")
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (start, end)):
            raise ValueError("'start' und 'end' müssen Zahlen sein.")
        # Kleine Ungenauigkeiten am Rand still korrigieren
        start, end = max(0.0, float(start)), min(float(duration), float(end))
        if end - start < 0.1:
            raise ValueError(f"Abschnitt {s} ist leer oder liegt außerhalb des Videos (Dauer {duration:.2f} s).")
        clean.append({"start": round(start, 3), "end": round(end, 3)})

    # Überlappende/aneinanderstoßende Abschnitte zusammenfassen, Reihenfolge beibehalten
    merged = [clean[0]]
    for s in clean[1:]:
        last = merged[-1]
        if last["start"] <= s["start"] <= last["end"] + 0.05:
            last["end"] = max(last["end"], s["end"])
        elif s["start"] < last["start"] < s["end"]:
            raise ValueError("Abschnitte überlappen sich.")
        else:
            merged.append(s)
    return merged


# ---------------------------------------------------------------- Eingabe für Claude bauen

def _format_transcript(transcript):
    segments = transcript.get("segments", [])
    if not segments:
        return "(Kein gesprochener Text im Video.)"

    total_words = sum(len(s["words"]) for s in segments)
    lines = []
    if total_words <= MAX_WORDS_WITH_TIMESTAMPS:
        lines.append("Format: [Satz-Start–Satz-Ende] Wort@Startzeit …")
        for s in segments:
            words = " ".join(f"{w['word']}@{w['start']:.2f}" for w in s["words"])
            lines.append(f"[{s['start']:.2f}–{s['end']:.2f}] {words}")
    else:
        lines.append("Format: [Satz-Start–Satz-Ende] Text")
        for s in segments:
            lines.append(f"[{s['start']:.2f}–{s['end']:.2f}] {s['text']}")
    return "\n".join(lines)


def _build_input(user_prompt, transcript, video_info, applied_operations):
    applied = describe(applied_operations) if applied_operations else "nichts"
    return (
        f"Videodauer: {video_info['duration']:.2f} s\n"
        f"Auflösung: {video_info['width']}×{video_info['height']}\n"
        f"Sprache: {transcript.get('language') or 'unbekannt'}\n"
        f"Bereits angewendet: {applied}\n\n"
        f"Transkript des aktuellen Videos:\n{_format_transcript(transcript)}\n\n"
        f"Wunsch des Nutzers: {user_prompt}"
    )


# ---------------------------------------------------------------- claude -p aufrufen

def _ask_claude(context, system_prompt=None, schema=None,
                instruction="Erstelle den Schnittplan für den Wunsch am Ende der Eingabe."):
    claude = claude_path()
    if not claude:
        raise UserError("Claude Code (`claude`) wurde nicht gefunden. Bitte `python3 check_setup.py` ausführen.")

    cmd = [
        claude, "-p", instruction,
        "--output-format", "json",
        "--model", MODEL,
        "--system-prompt", system_prompt or SYSTEM_PROMPT,
        "--json-schema", json.dumps(schema or SCHEMA),
        "--strict-mcp-config",          # keine MCP-Server laden (schneller)
        "--no-session-persistence",     # keine Sitzungen auf der Platte ansammeln
    ]
    # In einem leeren Ordner starten, damit Claude nicht die CLAUDE.md dieses Projekts
    # lädt und denkt, es soll am Code arbeiten.
    with tempfile.TemporaryDirectory() as empty_dir:
        try:
            result = subprocess.run(cmd, input=context, capture_output=True, text=True,
                                    timeout=TIMEOUT_SECONDS, cwd=empty_dir)
        except subprocess.TimeoutExpired:
            raise UserError("Claude hat zu lange gebraucht. Versuch es bitte nochmal.")

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        print(f"[planner] claude-Ausgabe nicht lesbar:\n{result.stdout[-2000:]}\n{result.stderr[-2000:]}")
        raise UserError(_explain_failure(result.stdout + result.stderr))

    if data.get("is_error") or result.returncode != 0:
        print(f"[planner] claude meldet Fehler: {json.dumps(data, ensure_ascii=False)[:2000]}")
        raise UserError(_explain_failure(str(data.get("result", ""))))

    # Bevorzugt das bereits geprüfte strukturierte Ergebnis, sonst den Text selbst parsen
    if isinstance(data.get("structured_output"), dict):
        return data["structured_output"]
    return _parse_json_text(data.get("result", ""))


def _parse_json_text(text):
    """Holt ein JSON-Objekt aus einem Text (auch wenn es in ```json … ``` steckt)."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


def _explain_failure(text):
    low = text.lower()
    if "login" in low or "not logged in" in low or "authenticat" in low or "api key" in low:
        return "Claude Code ist nicht eingeloggt. Bitte im Terminal einmal `claude` starten und einloggen."
    if "limit" in low or "quota" in low or "rate" in low:
        return "Dein Claude-Nutzungslimit ist gerade erreicht. Bitte später nochmal versuchen."
    if "overloaded" in low or "529" in low:
        return "Claude ist gerade überlastet. Bitte gleich nochmal versuchen."
    return "Claude hat nicht richtig geantwortet. Details stehen im Terminal-Fenster bzw. unter ~/Library/Logs/Video Editor.log."


# ---------------------------------------------------------------- Grafik-Plan (zweiter Schritt)

_STR = {"type": "string"}
GRAPHICS_SCHEMA = {
    "type": "object",
    "properties": {
        "scenes": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "start": {"type": "number"},
                "end": {"type": "number"},
                "layout": {"enum": LAYOUTS},
                "graphic": {
                    "type": "object",
                    "properties": {
                        "kind": {"enum": GRAPHIC_KINDS},
                        "text": _STR, "highlight": _STR, "kicker": _STR, "sub": _STR,
                        "value": {"type": "number"}, "prefix": _STR, "suffix": _STR, "label": _STR,
                        "step": _STR, "title": _STR,
                        "items": {"type": "array", "items": _STR},
                        "root": _STR, "children": {"type": "array", "items": _STR},
                        "src": _STR, "caption": _STR,
                    },
                    "required": ["kind"],
                    "additionalProperties": False,
                },
            },
            "required": ["start", "end", "layout"],
            "additionalProperties": False,
        }},
    },
    "required": ["scenes"],
    "additionalProperties": False,
}

GRAPHICS_PROMPT = """\
Du bist Motion-Designer für kurze Hochformat-Videos (TikTok/Reels) im Stil erfolgreicher \
Tech-Creator. Du bekommst die Wörter des fertig geschnittenen Videos mit Zeiten (Sekunden) und \
planst, welches Layout und welche animierte Grafik wann zu sehen ist.

Layouts:
- "split": Grafik oben, Sprecher unten in einer Karte. Das Standard-Layout, wenn eine Grafik läuft.
- "graphic": nur die Grafik bildfüllend (Ton läuft weiter). Für starke Momente, max. ~4 s am Stück.
- "card": Video als Karte auf unscharfem Hintergrund, Grafik darüber. Für Abwechslung.
- "full": Sprecher bildfüllend, ohne Grafik (oder mit einem kurzen "title" oben). Für persönliche Sätze.

Grafiken (Felder in Klammern):
- title {text, highlight, kicker?, sub?}: Kernaussage, max. 6 Wörter, highlight = 1–2 Wörter daraus \
in Akzentfarbe, kicker = kleines Label darüber (z. B. "Tipp"). Ideal für den Hook am Anfang.
- counter {value, prefix?, suffix?, label}: wenn eine Zahl/Statistik genannt wird; zählt hoch \
(z. B. value 160000, suffix "+", label "Sterne auf GitHub").
- step {step, title, highlight?, sub?}: bei "erstens/Schritt 2/als Nächstes" (step z. B. "Schritt 2").
- checklist {title, items[2–5]}: bei Aufzählungen; Punkte werden nacheinander abgehakt.
- tree {root, children[2–5]}: Struktur, Ordner, Bestandteile von etwas.
- prompt {text, label?}: wenn ein Befehl/Prompt/Frage zitiert wird; wird getippt.
- image {src, caption?}: NUR mit einer Datei aus der Liste "Verfügbare Assets", wenn sie zum Gesagten passt.

Regeln:
- Szenen beginnen und enden an Wortgrenzen (nutze die Zeiten), sortiert, ohne Überlappung, \
lückenlos von 0 bis zur Videodauer. Jede Szene 1,5–6 s.
- Start mit einem Hook: erste 2–4 s "split" + "title" mit der Kernaussage.
- Alle 2–5 s soll sich optisch etwas ändern. Etwa 60–75 % der Zeit mit Grafik, der Rest "full". \
Nie zwei gleiche Grafik-Arten direkt hintereinander, außer step → step.
- Die Grafik muss zu dem passen, was GENAU in dieser Szene gesagt wird. Texte kurz, in der Sprache \
des Videos, nichts erfinden, was nicht gesagt wird (Zahlen nur, wenn sie genannt werden).
- Letzte 2–3 s: "full" oder ein "title" mit dem Fazit.
- Wünsche des Nutzers (falls angegeben) haben Vorrang.
- Antworte ausschließlich mit dem JSON-Objekt.
"""


def plan_graphics(words, duration, assets, notes="", language=None):
    """
    Zweiter Claude-Aufruf: plant Layouts + Grafiken für das GESCHNITTENE Video.
    `words` = [{text, start, end}] mit Zeiten im geschnittenen Video.
    Gibt die rohen Szenen zurück (geprüft wird in motion.validate_scenes).
    """
    if words:
        lines, line = [], []
        for w in words:
            line.append(f"{w['text'].strip()}@{w['start']:.2f}")
            if len(line) >= 12 or w["text"].rstrip().endswith((".", "!", "?")):
                lines.append(" ".join(line))
                line = []
        if line:
            lines.append(" ".join(line))
        transcript = "\n".join(lines)
    else:
        transcript = "(Kein gesprochener Text.)"
    context = (
        f"Videodauer: {duration:.2f} s\n"
        f"Sprache des Videos: {language or 'siehe Wörter'} – ALLE Grafik-Texte in dieser Sprache schreiben!\n"
        f"Verfügbare Assets: {', '.join(assets) if assets else 'keine'}\n"
        f"Wünsche des Nutzers: {notes or 'keine'}\n\n"
        f"Wörter (Wort@Startzeit):\n{transcript}"
    )
    raw = _ask_claude(context, GRAPHICS_PROMPT, GRAPHICS_SCHEMA,
                      "Plane Layouts und Grafiken für dieses Video.")
    scenes = raw.get("scenes") if isinstance(raw, dict) else None
    if not isinstance(scenes, list) or not scenes:
        raise UserError("Claude hat keinen brauchbaren Grafik-Plan geliefert. Versuch es bitte nochmal.")
    return scenes


# ---------------------------------------------------------------- Kommandozeile

if __name__ == "__main__":
    import sys
    import time
    from pathlib import Path

    from .tools import probe
    from .transcribe import transcribe

    if len(sys.argv) != 3:
        print('Aufruf: .venv/bin/python -m editor.planner <video> "<Wunsch>"')
        sys.exit(1)

    video, wish = Path(sys.argv[1]), sys.argv[2]
    t = transcribe(video, Path(tempfile.gettempdir()) / "video-editor-transcripts")
    started = time.time()
    try:
        p = plan(wish, t, probe(video))
    except UserError as e:
        print("Fehler:", e)
        sys.exit(1)
    print(f"({time.time() - started:.1f} s)\n")
    print("Antwort:", p["reply"])
    print("Geplant:", describe(p["operations"]) or "–")
    print(json.dumps(p["operations"], indent=2, ensure_ascii=False))
