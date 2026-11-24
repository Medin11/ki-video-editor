"""
versions.py – verwaltet Projekte und Versionen.

Jedes hochgeladene Video bekommt einen eigenen Ordner in projects/:

    projects/<id>/
        original.mov      # das Original – wird NIE verändert
        v0.mp4            # nur falls das Original nicht im Browser abspielbar ist
        v1.mp4, v2.mp4…   # spätere bearbeitete Versionen
        transcripts/      # (ab Schritt 3) Transkript-Cache
        history.json      # Liste der Versionen + welche gerade aktiv ist

history.json sieht so aus:
    {
      "id": "...", "name": "Mein Video.mov", "created": "...",
      "current": 0,
      "versions": [
        {"n": 0, "file": "original.mov", "label": "Original", "parent": null,
         "prompt": null, "reply": null, "duration": 295.3, "operations": []},
        {"n": 1, "file": "v1.mp4", "label": "Pausen > 0.6 s entfernen", "parent": 0, ...}
      ]
    }

"parent" = auf welcher Version diese Version aufbaut. So geht beim Rückgängigmachen
nichts verloren: "Rückgängig" springt zur parent-Version, und eine neue Bearbeitung
danach baut einfach einen neuen Zweig – die alten Versionen bleiben im Verlauf.
"""

import json
import re
import secrets
import threading
from datetime import datetime
from pathlib import Path

from .tools import UserError

PROJECTS_DIR = Path(__file__).resolve().parent.parent / "projects"

# Formate, die Safari/Chrome direkt abspielen können
BROWSER_PLAYABLE = {".mp4", ".m4v", ".mov", ".webm"}

# Nur einfache IDs erlauben (schützt vor Pfad-Tricks wie "../")
_ID_PATTERN = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{4}$")

# Verhindert, dass zwei Threads gleichzeitig history.json schreiben
_lock = threading.Lock()


def project_dir(project_id):
    if not _ID_PATTERN.match(project_id or ""):
        raise UserError("Unbekanntes Projekt.")
    folder = PROJECTS_DIR / project_id
    if not folder.is_dir():
        raise UserError("Dieses Projekt gibt es nicht (mehr).")
    return folder


def new_project_id():
    return datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)


def create_project(file_storage):
    """Legt einen Projektordner an und speichert das hochgeladene Original."""
    ext = Path(file_storage.filename or "").suffix.lower() or ".mp4"
    project_id = new_project_id()
    folder = PROJECTS_DIR / project_id
    folder.mkdir(parents=True)
    (folder / "transcripts").mkdir()

    original = folder / f"original{ext}"
    file_storage.save(original)

    history = {
        "id": project_id,
        "name": Path(file_storage.filename or "video").name,
        "created": datetime.now().isoformat(timespec="seconds"),
        "current": 0,
        "versions": [],
    }
    save_history(project_id, history)
    return project_id, original


def load_history(project_id):
    path = project_dir(project_id) / "history.json"
    return json.loads(path.read_text(encoding="utf-8"))


def save_history(project_id, history):
    path = PROJECTS_DIR / project_id / "history.json"
    with _lock:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)  # atomar ersetzen – history.json ist nie halb geschrieben


def add_version(project_id, filename, label, duration, prompt=None, reply=None, operations=None):
    """Hängt eine neue Version an und macht sie zur aktuellen."""
    history = load_history(project_id)
    n = len(history["versions"])
    history["versions"].append({
        "n": n, "file": filename, "label": label,
        "parent": history["current"] if n > 0 else None,
        "prompt": prompt, "reply": reply, "duration": duration,
        "operations": operations or [],
        "created": datetime.now().isoformat(timespec="seconds"),
    })
    history["current"] = n
    save_history(project_id, history)
    return n


def version_path(project_id, n):
    """Pfad zur Videodatei einer bestimmten Version."""
    history = load_history(project_id)
    versions = history["versions"]
    if not 0 <= n < len(versions):
        raise UserError("Diese Version gibt es nicht.")
    return project_dir(project_id) / versions[n]["file"]


def parent_of(history, n):
    """Vorgänger einer Version (None beim Original)."""
    v = history["versions"][n]
    return v.get("parent", n - 1 if n > 0 else None)


def chain(history):
    """Alle Versionen vom Original bis zur aktuellen, in dieser Reihenfolge."""
    result, n = [], history["current"]
    while n is not None:
        result.append(history["versions"][n])
        n = parent_of(history, n)
    return list(reversed(result))


def undo(project_id):
    """Eine Version zurück (zum Vorgänger der aktuellen Version)."""
    history = load_history(project_id)
    parent = parent_of(history, history["current"])
    if parent is None:
        raise UserError("Das ist schon das Original – weiter zurück geht nicht.")
    history["current"] = parent
    save_history(project_id, history)
    return history


def select(project_id, n):
    """Eine beliebige Version zur aktuellen machen (Klick im Verlauf)."""
    history = load_history(project_id)
    if not isinstance(n, int) or not 0 <= n < len(history["versions"]):
        raise UserError("Diese Version gibt es nicht.")
    history["current"] = n
    save_history(project_id, history)
    return history
