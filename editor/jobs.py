"""
jobs.py – lässt lange Arbeiten (Transkribieren, Rendern…) im Hintergrund laufen.

Die Oberfläche bekommt sofort eine job_id zurück und fragt dann regelmäßig
GET /status/<job_id> ab. So hängt nichts, auch bei Videos über eine Stunde.

Benutzung:
    def arbeit(job, ...):
        job.update("Rendere…", progress=40)
        return {"reply": "Fertig!"}

    job_id = jobs.start(arbeit, ...)
"""

import secrets
import threading
import traceback

from .tools import UserError

_jobs = {}
_lock = threading.Lock()


class Job:
    def __init__(self):
        self.id = secrets.token_hex(8)
        self.state = "running"     # running | done | error
        self.message = "Starte…"   # Text für die Status-Anzeige
        self.progress = None       # 0–100 oder None (= unbestimmt)
        self.result = None         # Ergebnis, wenn fertig
        self.error = None          # freundliche Fehlermeldung

    def update(self, message, progress=None):
        self.message = message
        self.progress = None if progress is None else round(progress, 1)

    def to_dict(self):
        return {
            "id": self.id, "state": self.state, "message": self.message,
            "progress": self.progress, "result": self.result, "error": self.error,
        }


def start(func, *args, **kwargs):
    job = Job()
    with _lock:
        _jobs[job.id] = job

    def runner():
        try:
            job.result = func(job, *args, **kwargs)
            job.state = "done"
        except UserError as e:
            job.error = str(e)
            job.state = "error"
        except Exception:
            # Unerwarteter Fehler: Details in die Konsole, nette Meldung in die UI
            traceback.print_exc()
            job.error = "Da ist leider etwas schiefgegangen. Details stehen im Terminal-Fenster bzw. unter ~/Library/Logs/Video Editor.log."
            job.state = "error"

    threading.Thread(target=runner, daemon=True).start()
    return job.id


def get(job_id):
    with _lock:
        return _jobs.get(job_id)
