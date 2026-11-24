"""
transcribe.py – wandelt die Sprache im Video in Text um (mit Zeitstempel für jedes Wort).

- Apple Silicon: mlx-whisper (nutzt die Grafikeinheit des Macs, sehr schnell)
- Intel-Mac:     faster-whisper (Fallback)
- Die Sprache wird automatisch erkannt.
- Ergebnisse werden gecacht: Jede Videodatei bekommt einen "Fingerabdruck" (Hash).
  Ist für diesen Hash schon ein Transkript da, wird nicht nochmal transkribiert.

Testen von der Kommandozeile:
    .venv/bin/python -m editor.transcribe pfad/zum/video.mp4

Format des Ergebnisses:
    {
      "language": "de",
      "duration": 123.4,
      "segments": [
        {"start": 0.0, "end": 3.2, "text": "Hallo zusammen.",
         "words": [{"word": "Hallo", "start": 0.0, "end": 0.4}, ...]}
      ]
    }
"""

import hashlib
import importlib
import json
import os
import platform
import tempfile
import threading
from pathlib import Path

import numpy as np

from .tools import UserError, probe, run_ffmpeg

# Keine Download-Balken von Hugging Face im Terminal
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

# ---------------------------------------------------------------- Einstellungen

IS_APPLE_SILICON = platform.machine() == "arm64"

# Modell: "large-v3-turbo" = sehr gute Qualität (auch Deutsch) und trotzdem schnell.
# Wird beim ersten Mal einmalig heruntergeladen (ca. 1,6 GB).
MLX_MODEL = "mlx-community/whisper-large-v3-turbo"
FASTER_WHISPER_MODEL = "large-v3-turbo"

# Whisper lässt Füllwörter gerne weg. Ein kurzer "Beispieltext" mit Füllwörtern
# bringt es dazu, "ähm", "äh", "uh", "um" mit aufzuschreiben – das brauchen wir
# zum Entfernen der Füllwörter.
FILLER_PROMPT = "Ähm, also, äh, ich meine, hm. Umm, uh, like, you know."

SAMPLE_RATE = 16000  # Whisper arbeitet immer mit 16 kHz Mono

# Nur ein Transkript gleichzeitig (schont Speicher und Grafikeinheit)
_lock = threading.Lock()


# ---------------------------------------------------------------- Öffentliche Funktion

def transcribe(video_path, cache_dir, on_status=None):
    """
    Liefert das Transkript für `video_path` – aus dem Cache oder frisch erstellt.
    on_status(text, prozent_oder_None) wird für die Statusanzeige aufgerufen.
    """
    status = on_status or (lambda msg, pct=None: None)
    video_path, cache_dir = Path(video_path), Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    status("Prüfe, ob es schon ein Transkript gibt…")
    file_hash = _file_hash(video_path)
    cache_file = cache_dir / f"{file_hash}.json"

    # Lock VOR dem zweiten Cache-Blick: Falls gerade im Hintergrund dasselbe Video
    # transkribiert wird, warten wir darauf, statt doppelt zu arbeiten.
    if not cache_file.exists() and _lock.locked():
        status("Warte auf laufende Transkription…")
    with _lock:
        if cache_file.exists():
            return json.loads(cache_file.read_text(encoding="utf-8"))

        info = probe(video_path)
        if not info["has_audio"]:
            result = {"language": None, "duration": info["duration"], "segments": []}
        else:
            audio = _extract_audio(video_path, info["duration"], status)
            if IS_APPLE_SILICON:
                result = _transcribe_mlx(audio, info["duration"], status)
            else:
                result = _transcribe_faster_whisper(audio, info["duration"], status)

        result["hash"] = file_hash
        tmp = cache_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(cache_file)
        return result


def word_count(transcript):
    return sum(len(s["words"]) for s in transcript["segments"])


# ---------------------------------------------------------------- Schritte

def _file_hash(path):
    """Fingerabdruck der Datei (liest sie einmal komplett – bei 4 GB ein paar Sekunden)."""
    h = hashlib.blake2b(digest_size=16)
    with open(path, "rb") as f:
        while chunk := f.read(8 * 1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def _extract_audio(video_path, duration, status):
    """Tonspur als 16-kHz-Mono-Rohdaten holen und als numpy-Array zurückgeben."""
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "audio.f32"
        run_ffmpeg(
            ["-i", str(video_path), "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE),
             "-f", "f32le", str(raw)],
            duration=duration,
            on_progress=lambda p: status("Hole Tonspur aus dem Video…", p),
        )
        return np.fromfile(raw, dtype=np.float32)


def _transcribe_mlx(audio, duration, status):
    import mlx_whisper
    from huggingface_hub import snapshot_download

    # Modell einmalig herunterladen (danach liegt es im Cache unter ~/.cache/huggingface)
    try:
        snapshot_download(MLX_MODEL, local_files_only=True)
    except Exception:
        status("Lade Whisper-Modell herunter (einmalig, ca. 1,6 GB)…")
        snapshot_download(MLX_MODEL)

    status("Transkribiere…", 0)
    with _report_progress(lambda p: status("Transkribiere…", p)):
        raw = mlx_whisper.transcribe(
            audio,
            path_or_hf_repo=MLX_MODEL,
            word_timestamps=True,
            initial_prompt=FILLER_PROMPT,
            condition_on_previous_text=False,       # verhindert Wiederholungsschleifen bei langen Videos
            hallucination_silence_threshold=2.0,    # erfindet in langen Stillen keinen Text
            verbose=None,
        )
    return _clean(raw, duration)


def _transcribe_faster_whisper(audio, duration, status):
    from faster_whisper import WhisperModel

    status("Lade Whisper-Modell (beim ersten Mal wird es heruntergeladen)…")
    model = WhisperModel(FASTER_WHISPER_MODEL, device="cpu", compute_type="int8")
    segments, info = model.transcribe(
        audio,
        word_timestamps=True,
        initial_prompt=FILLER_PROMPT,
        condition_on_previous_text=False,
        hallucination_silence_threshold=2.0,
    )
    out = []
    for seg in segments:  # Generator: Segmente kommen nach und nach
        if duration:
            status("Transkribiere…", min(100, seg.end / duration * 100))
        out.append({
            "start": seg.start, "end": seg.end, "text": seg.text,
            "words": [{"word": w.word, "start": w.start, "end": w.end} for w in (seg.words or [])],
        })
    return _clean({"language": info.language, "segments": out}, duration)


def _clean(raw, duration):
    """Bringt das Whisper-Ergebnis in unser schlankes Format (gerundete Zeiten, ohne Ballast)."""
    segments = []
    for seg in raw.get("segments", []):
        words = [
            {"word": w["word"].strip(), "start": round(w["start"], 2), "end": round(w["end"], 2)}
            for w in seg.get("words", []) if w["word"].strip()
        ]
        if not words:
            continue
        segments.append({
            "start": words[0]["start"], "end": words[-1]["end"],
            "text": seg["text"].strip(), "words": words,
        })
    return {"language": raw.get("language"), "duration": round(duration, 2), "segments": segments}


# ---------------------------------------------------------------- Fortschritt von mlx-whisper abgreifen

class _report_progress:
    """
    mlx-whisper zeigt seinen Fortschritt nur als Terminal-Balken (tqdm).
    Für die Dauer der Transkription ersetzen wir diesen Balken durch einen,
    der den Prozentwert an unsere Statusanzeige weitergibt.
    """

    def __init__(self, callback):
        self.callback = callback
        self.module = importlib.import_module("mlx_whisper.transcribe")

    def __enter__(self):
        callback = self.callback

        class Bar:
            def __init__(self, total=None, **_):
                self.total, self.n = total or 0, 0

            def update(self, n):
                self.n += n
                if self.total:
                    callback(min(100, self.n / self.total * 100))

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        self.original = self.module.tqdm
        self.module.tqdm = type("tqdm_shim", (), {"tqdm": Bar})
        return self

    def __exit__(self, *_):
        self.module.tqdm = self.original
        return False


# ---------------------------------------------------------------- Kommandozeile

if __name__ == "__main__":
    import sys
    import time

    if len(sys.argv) != 2:
        print("Aufruf: .venv/bin/python -m editor.transcribe <video>")
        sys.exit(1)

    video = Path(sys.argv[1])
    start = time.time()
    try:
        t = transcribe(video, Path(tempfile.gettempdir()) / "video-editor-transcripts",
                       on_status=lambda m, p=None: print(f"  {m} {'' if p is None else f'{p:.0f} %'}"))
    except UserError as e:
        print("Fehler:", e)
        sys.exit(1)

    print(f"\nSprache: {t['language']} · {word_count(t)} Wörter · {time.time() - start:.1f} s\n")
    for seg in t["segments"]:
        print(f"[{seg['start']:7.2f} – {seg['end']:7.2f}] {seg['text']}")
