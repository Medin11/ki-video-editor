"""
app.py – der lokale Webserver des KI-Video-Editors.

Starten:  .venv/bin/python app.py
Dann im Browser:  http://127.0.0.1:5050  (öffnet sich automatisch)

Der Server läuft NUR auf 127.0.0.1 – also nur auf diesem Mac, nicht im Netzwerk.
(Port 5000 ist auf dem Mac vom AirPlay-Empfänger belegt, deshalb 5050.)
"""

import shutil
import socket
import subprocess
import threading
import webbrowser
from pathlib import Path

from flask import Flask, jsonify, request, send_file

from editor import jobs, planner, render, versions
from editor.transcribe import transcribe, word_count
from editor.tools import UserError, probe, run_ffmpeg

HOST = "127.0.0.1"
PREFERRED_PORT = 5050
STATIC_DIR = Path(__file__).resolve().parent / "static"
EXPORT_DIR = Path.home() / "Movies" / "Video Editor Exports"

# Video-Codecs, die Safari/Chrome auf dem Mac direkt abspielen können
PLAYABLE_CODECS = {"h264", "hevc", "vp9", "av1"}

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")


# ---------------------------------------------------------------- Fehler

@app.errorhandler(UserError)
def handle_user_error(e):
    return jsonify(error=str(e)), 400


@app.errorhandler(Exception)
def handle_unexpected(e):
    # HTTP-Fehler wie 404 normal durchreichen
    if hasattr(e, "code") and hasattr(e, "get_response"):
        message = "Nicht gefunden." if e.code == 404 else "Ungültige Anfrage."
        return jsonify(error=message), e.code
    app.logger.exception(e)
    return jsonify(error="Da ist leider etwas schiefgegangen. Details stehen im Terminal-Fenster bzw. unter ~/Library/Logs/Video Editor.log."), 500


# ---------------------------------------------------------------- Seiten & API

@app.get("/ping")
def ping():
    """Erkennungszeichen: So merkt ein zweiter Start, dass der Editor schon läuft."""
    return jsonify(app="ki-video-editor")


@app.get("/")
def index():
    return send_file(STATIC_DIR / "index.html")


@app.post("/upload")
def upload():
    """Video entgegennehmen, Projekt anlegen und im Hintergrund vorbereiten."""
    file = request.files.get("video")
    if not file or not file.filename:
        raise UserError("Es wurde keine Datei mitgeschickt.")

    project_id, original = versions.create_project(file)
    job_id = jobs.start(_prepare_project, project_id, original)
    return jsonify(project_id=project_id, job_id=job_id)


def _prepare_project(job, project_id, original):
    """
    Prüft das Video und legt Version 0 an.
    Kann der Browser das Original direkt abspielen, ist Version 0 das Original selbst.
    Sonst wird einmalig eine abspielbare Kopie (v0.mp4) erstellt – das Original bleibt unberührt.
    """
    job.update("Prüfe Video…")
    try:
        info = probe(original)
    except UserError:
        shutil.rmtree(original.parent, ignore_errors=True)  # kaputtes Projekt wegräumen
        raise

    playable = (original.suffix.lower() in versions.BROWSER_PLAYABLE
                and info["codec"] in PLAYABLE_CODECS)
    if playable:
        filename = original.name
    else:
        job.update("Wandle Video in ein abspielbares Format um…", progress=0)
        filename = "v0.mp4"
        run_ffmpeg(
            ["-i", str(original),
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
             str(original.parent / filename)],
            duration=info["duration"],
            on_progress=lambda p: job.update("Wandle Video in ein abspielbares Format um…", p),
        )

    versions.add_version(project_id, filename, "Original", info["duration"])

    # Schon mal im Hintergrund transkribieren – dann geht der erste Prompt schneller.
    # (Die Oberfläche wartet nicht darauf.)
    jobs.start(_transcribe_current, project_id)
    return {"project": versions.load_history(project_id)}


def _transcribe_current(job, project_id):
    """Transkript der aktuellen Version holen (aus dem Cache oder neu erstellen)."""
    history = versions.load_history(project_id)
    path = versions.version_path(project_id, history["current"])
    return transcribe(path, versions.project_dir(project_id) / "transcripts",
                      on_status=lambda msg, pct=None: job.update(msg, pct))


@app.get("/project/<project_id>")
def get_project(project_id):
    """Projekt-Infos (Versionen, aktuelle Version) – z. B. nach einem Neuladen der Seite."""
    history = versions.load_history(project_id)
    if not history["versions"]:
        raise UserError("Dieses Projekt wurde nie fertig vorbereitet.")
    return jsonify(project=history)


@app.post("/prompt")
def prompt():
    """Prompt entgegennehmen und Bearbeitung im Hintergrund starten."""
    data = request.get_json(silent=True) or {}
    project_id = data.get("project_id")
    text = (data.get("text") or "").strip()
    versions.project_dir(project_id)  # prüft, ob es das Projekt gibt
    if not text:
        raise UserError("Bitte schreib, was mit dem Video passieren soll.")

    job_id = jobs.start(_run_prompt, project_id, text)
    return jsonify(job_id=job_id)


def _run_prompt(job, project_id, text):
    # 1. Transkript der aktuellen Version (gecacht)
    transcript = _transcribe_current(job, project_id)

    # 2. Claude nach einem Schnittplan fragen
    job.update("Claude plant Schnitte…")
    history = versions.load_history(project_id)
    current_path = versions.version_path(project_id, history["current"])
    info = probe(current_path)
    plan = planner.plan(text, transcript, info, applied_operations=_applied_operations(history))

    # Nichts zu tun (Frage beantwortet oder Wunsch nicht machbar) -> nur antworten
    if not plan["operations"]:
        return {"reply": plan["reply"], "project": history}

    # 3. Plan rendern -> neue Version (das Original bleibt unberührt)
    n = len(history["versions"])
    filename = f"v{n}.mp4"
    output = versions.project_dir(project_id) / filename
    try:
        stats = render.render(current_path, output, plan["operations"], transcript, info,
                              on_status=lambda msg, pct=None: job.update(msg, pct))
    except Exception:
        output.unlink(missing_ok=True)   # halbfertige Datei wegräumen
        raise

    reply = f"{plan['reply']} {render.summarize(stats)}"
    versions.add_version(project_id, filename, planner.describe(plan["operations"]),
                         stats["new_duration"], prompt=text, reply=reply,
                         operations=plan["operations"])

    # Neue Version schon mal im Hintergrund transkribieren (für den nächsten Prompt)
    jobs.start(_transcribe_current, project_id)
    return {"reply": reply, "project": versions.load_history(project_id)}


def _applied_operations(history):
    """Alle Operationen, die schon im aktuellen Video stecken (entlang der Versions-Kette)."""
    ops = []
    for v in versions.chain(history):
        ops.extend(v.get("operations", []))
    return ops


@app.post("/undo")
def undo():
    """Eine Version zurück."""
    data = request.get_json(silent=True) or {}
    return jsonify(project=versions.undo(data.get("project_id")))


@app.post("/select")
def select():
    """Eine Version aus dem Verlauf wieder zur aktuellen machen."""
    data = request.get_json(silent=True) or {}
    return jsonify(project=versions.select(data.get("project_id"), data.get("n")))


@app.get("/export/<project_id>")
def export(project_id):
    """
    Speichert die aktuelle Version nach ~/Movies/Video Editor Exports/ und zeigt sie im Finder.
    Mit ?download=1 lädt der Browser die Datei stattdessen herunter.
    """
    history = versions.load_history(project_id)
    n = history["current"]
    source = versions.version_path(project_id, n)
    nice_name = f"{Path(history['name']).stem} – v{n}{source.suffix}"

    if request.args.get("download"):
        return send_file(source, as_attachment=True, download_name=nice_name)

    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    target = EXPORT_DIR / nice_name
    counter = 2
    while target.exists():   # nichts überschreiben
        target = EXPORT_DIR / f"{Path(nice_name).stem} ({counter}){source.suffix}"
        counter += 1

    # "cp -c" nutzt APFS-Clones: auch riesige Dateien sind sofort kopiert, ohne extra Speicherplatz
    if subprocess.run(["cp", "-c", str(source), str(target)], capture_output=True).returncode != 0:
        shutil.copy2(source, target)
    subprocess.run(["open", "-R", str(target)])   # im Finder zeigen
    return jsonify(path=str(target), display=str(target).replace(str(Path.home()), "~"))


@app.get("/status/<job_id>")
def status(job_id):
    job = jobs.get(job_id)
    if job is None:
        raise UserError("Diesen Auftrag kenne ich nicht (wurde der Server neu gestartet?).")
    return jsonify(job.to_dict())


@app.get("/video/<project_id>/<int:n>")
def video(project_id, n):
    """Liefert ein Video an den Player. conditional=True erlaubt Range-Requests (Spulen)."""
    path = versions.version_path(project_id, n)
    return send_file(path, conditional=True)


# ---------------------------------------------------------------- Start

def find_running_instance(start):
    """Läuft der Editor schon (z. B. doppelt gestartet)? Dann dessen Adresse zurückgeben."""
    import json
    import urllib.request
    for port in range(start, start + 10):
        try:
            with urllib.request.urlopen(f"http://{HOST}:{port}/ping", timeout=0.5) as r:
                if json.load(r).get("app") == "ki-video-editor":
                    return f"http://{HOST}:{port}"
        except Exception:
            continue
    return None


def find_free_port(start):
    """Ersten freien Port ab `start` suchen."""
    for port in range(start, start + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex((HOST, port)) != 0:
                return port
    raise RuntimeError("Kein freier Port gefunden.")


if __name__ == "__main__":
    import sys

    # Gestartet von der Mac-App? Dann beenden, sobald die App weg ist (auch nach einem Absturz).
    if "--managed" in sys.argv:
        import os
        parent = os.getppid()

        def watch_parent():
            while True:
                if os.getppid() != parent:
                    os._exit(0)
                threading.Event().wait(1)

        threading.Thread(target=watch_parent, daemon=True).start()

    running = find_running_instance(PREFERRED_PORT)
    if running:
        print(f"\n🎬 Der Editor läuft schon auf {running} – öffne nur den Browser.\n")
        if "--no-browser" not in sys.argv:
            webbrowser.open(running)
        sys.exit(0)

    port = find_free_port(PREFERRED_PORT)
    url = f"http://{HOST}:{port}"
    print(f"\n🎬 KI-Video-Editor läuft auf {url}")
    print("   Zum Beenden: dieses Fenster schließen oder Ctrl+C drücken.\n", flush=True)

    if "--no-browser" not in sys.argv:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    # Terminal aufgeräumt halten: keine Zeile pro Anfrage (die Oberfläche fragt ständig den
    # Status ab) und kein "development server"-Hinweis – der Server läuft ja nur lokal für dich.
    import logging
    import flask.cli
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    flask.cli.show_server_banner = lambda *args, **kwargs: None

    # threaded=True: Status-Abfragen und Video-Streaming laufen parallel zu Uploads
    app.run(host=HOST, port=port, threaded=True, debug=False)
