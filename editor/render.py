"""
render.py – setzt den Schnittplan von Claude mit ffmpeg um.

Ablauf:
  1. Aus den Schnitt-Operationen (keep_segments, remove_pauses, remove_fillers) wird eine
     Liste von Abschnitten berechnet, die BEHALTEN werden ("Behalten-Liste").
  2. Daraus wird EIN ffmpeg-Filter gebaut, der in dieser Reihenfolge arbeitet:
        Schnitte → Tempo (speed) → Format (aspect) → Untertitel (subtitles) → Blende (fade)
  3. ffmpeg rendert alles in einem Durchgang in eine neue Datei (Original bleibt unberührt).

Untertitel: Die Wort-Zeitstempel beziehen sich auf das Video VOR dem Schneiden.
Deshalb werden sie durch die Schnitte und das Tempo "umgerechnet" (siehe _TimeMap).

Erweiterbar: Neue Effekte (Musik, Zoom, Logo, Farbfilter…) werden später als weitere
Bausteine in _build_filter() eingehängt.
"""

import re
import tempfile
from pathlib import Path

from .tools import UserError, probe, run_ffmpeg

# ---------------------------------------------------------------- Einstellungen

PAUSE_BUFFER = 0.15          # so viel Stille bleibt am Rand jeder entfernten Pause stehen (Sekunden)
SILENCE_THRESHOLD_DB = -35   # leiser als das gilt als Stille
AUDIO_FADE = 0.01            # 10 ms weiches Ein-/Ausblenden an jeder Schnittstelle (verhindert Knackser)
MIN_PIECE = 0.05             # kürzere Reststücke werden verworfen

# Füllwörter (klein geschrieben, ohne Satzzeichen). Bewusst NICHT dabei:
# "im", "ja", "er", "also" – das sind meistens echte Wörter.
FILLER_WORDS = {
    "ähm", "ähhm", "ähmm", "äh", "ähh", "öhm", "öh", "hm", "hmm", "hmmm", "mhm", "mh",
    "uh", "uhh", "um", "umm", "uhm", "erm", "eh", "ehm",
}

# Zielgrößen für die Formate
ASPECT_SIZES = {"9:16": (1080, 1920), "1:1": (1080, 1080), "16:9": (1920, 1080)}

# --- Referenz-Stil (abgeleitet aus den Videos im Ordner references/) ---
# Alle drei Referenzen: sehr enge Jump-Cuts (praktisch keine Pause > 0,15 s),
# Lautheit ca. -14 LUFS, Untertitel mit 1–2 Wörtern in GROSSBUCHSTABEN,
# dicke Schrift, weiß auf dunkler Box, gesprochenes Wort in Koralle.
REF_ACCENT = "&H5A73E8&"     # Koralle #E8735A (ASS schreibt Farben als Blau-Grün-Rot)
REF_FONT = "Arial Black"
ZOOM_MIN_SHOT = 2.5          # Punch-Zoom wechselt frühestens nach so vielen Sekunden (an einem Schnitt)

# Encoder-Einstellungen (siehe CLAUDE.md: präzise Schnitte -> neu encodieren)
ENCODE_ARGS = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]


# ---------------------------------------------------------------- Öffentliche Funktion

def render(input_path, output_path, operations, transcript, info, on_status=None):
    """
    Rendert `input_path` mit den `operations` nach `output_path`.
    Gibt Statistiken zurück: Pausen/Füllwörter entfernt, alte/neue Dauer.
    """
    status = on_status or (lambda msg, pct=None: None)
    ops = {op["type"]: op for op in operations}
    duration = info["duration"]
    stats = {"old_duration": duration, "pauses": 0, "fillers": 0}

    motion_op = ops.pop("motion_graphics", None)   # animierte Grafiken: zweiter Schritt mit Remotion
    if motion_op:
        from . import motion
        motion.check_ready()   # früh scheitern, bevor lange geschnitten wird

    # Prüfen, ob die gewünschten Operationen überhaupt gehen
    has_speech = bool(transcript.get("segments"))
    if "subtitles" in ops and not has_speech:
        raise UserError("In diesem Video wird nicht gesprochen – Untertitel sind daher nicht möglich.")
    if "remove_fillers" in ops and not has_speech:
        raise UserError("In diesem Video wird nicht gesprochen – es gibt keine Füllwörter.")
    if "remove_pauses" in ops and not info["has_audio"]:
        raise UserError("Das Video hat keinen Ton – Pausen kann ich so nicht erkennen.")

    # 1. Behalten-Liste berechnen
    keep = [(s["start"], s["end"]) for s in ops["keep_segments"]["segments"]] \
        if "keep_segments" in ops else [(0.0, duration)]

    removals = []
    if "remove_pauses" in ops:
        pauses = _find_pauses(input_path, transcript, duration, ops["remove_pauses"]["min_pause"], status,
                              buffer=ops["remove_pauses"].get("buffer", PAUSE_BUFFER))
        removals += pauses
        stats["pauses"] = sum(1 for p in pauses if _overlaps_any(p, keep))
    if "remove_fillers" in ops:
        fillers = _find_fillers(transcript)
        removals += fillers
        stats["fillers"] = sum(1 for f in fillers if _overlaps_any(f, keep))

    keep = _subtract(keep, removals)
    keep = _snap_to_frames(keep, info["fps"], duration)
    if not keep:
        raise UserError("Nach dem Schneiden bliebe vom Video nichts übrig.")

    speed = ops["speed"]["factor"] if "speed" in ops else 1.0
    new_duration = sum(b - a for a, b in keep) / speed
    stats["new_duration"] = new_duration

    if "fade" in ops and ops["fade"]["in"] + ops["fade"]["out"] > new_duration:
        raise UserError("Das Video ist nach dem Schneiden zu kurz für diese Blende.")

    # 2. + 3. Filter bauen und rendern
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        out_w, out_h = ASPECT_SIZES[ops["aspect"]["ratio"]] if "aspect" in ops else (info["width"], info["height"])

        ass_file = None
        if "subtitles" in ops:
            ass_file = tmp / "subs.ass"
            timemap = _TimeMap(keep, speed)
            ass_file.write_text(
                _make_ass(transcript, timemap, ops["subtitles"]["style"], out_w, out_h), encoding="utf-8")

        graph = _build_filter(keep, info, ops, speed, new_duration, ass_file, full_video=(keep == [(0.0, duration)]))
        script = tmp / "filter.txt"
        script.write_text(graph, encoding="utf-8")

        # Mit animierten Grafiken ist ffmpeg nur Schritt 1 (Schnitt) – das Ergebnis geht an Remotion
        stage_out = tmp / "cut.mp4" if motion_op else output_path
        label = "Schneide…" if motion_op else "Rendere…"
        maps = ["-map", "[vout]"] + (["-map", "[aout]"] if info["has_audio"] else [])
        status(label, 0)
        run_ffmpeg(
            ["-i", str(input_path), "-/filter_complex", str(script), *maps, *ENCODE_ARGS, str(stage_out)],
            duration=new_duration,
            on_progress=lambda p: status(label, p),
        )

        if motion_op:
            _render_motion(stage_out, output_path, motion_op, transcript, keep, speed, tmp, status)
    return stats


def _render_motion(cut_path, output_path, motion_op, transcript, keep, speed, tmp, status):
    """Schritt 2: Claude plant Grafiken, Remotion rendert Layouts + Grafiken + Untertitel."""
    from . import motion, planner

    cut_info = probe(cut_path)
    words = _mapped_words(transcript, _TimeMap(keep, speed))

    status("Claude plant Grafiken…")
    raw = planner.plan_graphics(words, cut_info["duration"], motion.list_assets(), motion_op.get("notes", ""),
                               language=transcript.get("language"))
    scenes = motion.validate_scenes(raw, cut_info["duration"])
    print(f"[render] Grafik-Plan: {sum(1 for s in scenes if s.get('graphic'))} Grafiken in {len(scenes)} Szenen")

    captions = motion.caption_groups(words) if motion_op.get("captions", True) and words else None
    status("Rendere Animationen…", 0)
    motion.render_motion(cut_path, output_path, cut_info, scenes, captions, motion_op.get("theme", "coral"),
                         tmp / "motion", on_progress=lambda p: status("Rendere Animationen…", p))


def summarize(stats):
    """Kurzer Satz für die Oberfläche, z. B. '12 Pausen entfernt · Video jetzt 3:41 statt 4:55.'"""
    parts = []
    if stats.get("pauses"):
        parts.append(f"{stats['pauses']} Pause{'n' if stats['pauses'] != 1 else ''} entfernt")
    if stats.get("fillers"):
        parts.append(f"{stats['fillers']} Füllw{'örter' if stats['fillers'] != 1 else 'ort'} entfernt")
    old, new = stats["old_duration"], stats["new_duration"]
    if abs(old - new) >= 0.5:
        parts.append(f"Video jetzt {format_time(new)} statt {format_time(old)}")
    else:
        parts.append(f"Länge {format_time(new)}")
    return " · ".join(parts) + "."


def format_time(seconds):
    seconds = int(round(seconds))
    h, m, s = seconds // 3600, seconds % 3600 // 60, seconds % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# ---------------------------------------------------------------- Pausen & Füllwörter finden

def _find_pauses(input_path, transcript, duration, min_pause, status, buffer=PAUSE_BUFFER):
    """
    Pausen = Lücken zwischen gesprochenen Wörtern (aus dem Transkript)
             ODER Stille laut ffmpeg-silencedetect – jeweils länger als min_pause.
    Zurück kommen die zu ENTFERNENDEN Stücke (schon um den Puffer verkleinert).
    """
    candidates = []

    # a) Lücken im Transkript (inkl. Anfang und Ende des Videos)
    words = [w for s in transcript.get("segments", []) for w in s["words"]]
    if words:
        edges = [0.0] + [t for w in words for t in (w["start"], w["end"])] + [duration]
        for gap_start, gap_end in zip(edges[0::2], edges[1::2]):
            if gap_end - gap_start >= min_pause:
                candidates.append((gap_start, gap_end))

    # b) Stille im Ton
    log = run_ffmpeg(
        ["-i", str(input_path), "-vn", "-af", f"silencedetect=n={SILENCE_THRESHOLD_DB}dB:d={min_pause}",
         "-f", "null", "-"],
        duration=duration,
        on_progress=lambda p: status("Suche Pausen…", p),
    )
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", log)]
    ends = [float(x) for x in re.findall(r"silence_end: (-?[\d.]+)", log)]
    if len(ends) < len(starts):      # Stille bis zum Ende des Videos
        ends.append(duration)
    candidates += [(max(0.0, a), min(duration, b)) for a, b in zip(starts, ends)]

    # Zusammenführen, dann an beiden Rändern den Puffer stehen lassen
    pauses = []
    for a, b in _merge(candidates):
        # Am Videoanfang/-ende braucht es keinen Puffer nach außen
        a2 = a if a <= 0.01 else a + buffer
        b2 = b if b >= duration - 0.01 else b - buffer
        if b2 - a2 >= MIN_PIECE:
            pauses.append((a2, b2))
    return pauses


def _find_fillers(transcript):
    """Zeitbereiche aller Füllwörter."""
    found = []
    for seg in transcript.get("segments", []):
        for w in seg["words"]:
            clean = re.sub(r"[^\wäöüß]", "", w["word"].lower())
            if clean in FILLER_WORDS:
                found.append((w["start"], w["end"]))
    return found


# ---------------------------------------------------------------- Intervall-Rechnerei

def _merge(intervals):
    """Überlappende Intervalle zusammenfassen (Ergebnis sortiert)."""
    out = []
    for a, b in sorted(intervals):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _subtract(keep, removals):
    """Entfernt alle `removals` aus den `keep`-Abschnitten (Reihenfolge von keep bleibt)."""
    removals = _merge(removals)
    result = []
    for a, b in keep:
        pieces = [(a, b)]
        for ra, rb in removals:
            next_pieces = []
            for pa, pb in pieces:
                if rb <= pa or ra >= pb:          # keine Überschneidung
                    next_pieces.append((pa, pb))
                    continue
                if ra > pa:
                    next_pieces.append((pa, ra))
                if rb < pb:
                    next_pieces.append((rb, pb))
            pieces = next_pieces
        result += [(pa, pb) for pa, pb in pieces if pb - pa >= MIN_PIECE]
    return result


def _overlaps_any(interval, keep):
    a, b = interval
    return any(a < kb and b > ka for ka, kb in keep)


def _snap_to_frames(keep, fps, duration):
    """Schnittpunkte auf ganze Videobilder runden – so bleiben Bild und Ton exakt synchron."""
    snapped = []
    for a, b in keep:
        a = round(round(a * fps) / fps, 4)
        b = min(duration, round(round(b * fps) / fps, 4))
        if b - a >= MIN_PIECE:
            snapped.append((a, b))
    return snapped


# ---------------------------------------------------------------- ffmpeg-Filter bauen

def _build_filter(keep, info, ops, speed, new_duration, ass_file, full_video):
    has_audio = info["has_audio"]
    lines = []

    # --- Bild vorbereiten: HDR (iPhone) in normale Farben umrechnen
    v_in = "[0:v]"
    if info.get("hdr"):
        lines.append("[0:v]zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
                     "tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p[vsdr]")
        v_in = "[vsdr]"

    # --- Schnitte
    if full_video:
        v, a = v_in, "[0:a]"
    else:
        n = len(keep)
        lines.append(f"{v_in}split={n}" + "".join(f"[vs{i}]" for i in range(n)))
        if has_audio:
            lines.append(f"[0:a]asplit={n}" + "".join(f"[as{i}]" for i in range(n)))
        concat_inputs = ""
        zoomed = _zoom_pattern(keep) if "punch_zoom" in ops else [False] * n
        zoom = ops.get("punch_zoom", {}).get("amount", 1.12)
        w, h = info["width"], info["height"]
        for i, (start, end) in enumerate(keep):
            # Punch-Zoom: jede zweite "Einstellung" etwas näher ran (Bildmitte, leicht nach oben = Gesicht)
            zoom_filter = (f",crop=iw/{zoom}:ih/{zoom}:(iw-ow)/2:(ih-oh)*0.4,scale={w}:{h}:flags=lanczos,setsar=1"
                           if zoomed[i] else "")
            lines.append(f"[vs{i}]trim=start={start}:end={end},setpts=PTS-STARTPTS{zoom_filter}[v{i}]")
            concat_inputs += f"[v{i}]"
            if has_audio:
                length = end - start
                fade = min(AUDIO_FADE, length / 4)
                lines.append(
                    f"[as{i}]atrim=start={start}:end={end},asetpts=PTS-STARTPTS,"
                    f"afade=t=in:d={fade},afade=t=out:st={max(0, length - fade):.4f}:d={fade}[a{i}]")
                concat_inputs += f"[a{i}]"
        lines.append(f"{concat_inputs}concat=n={n}:v=1:a={1 if has_audio else 0}"
                     + ("[vcut][acut]" if has_audio else "[vcut]"))
        v, a = "[vcut]", "[acut]"

    # --- Video-Kette: Tempo → Format → Untertitel → Blende
    vf = []
    af = []
    if speed != 1.0:
        vf.append(f"setpts=PTS/{speed}")
        af.append(f"atempo={speed}")
    blur_fit = "aspect" in ops and ops["aspect"].get("fit") == "blur"
    if "aspect" in ops and not blur_fit:
        w, h = ASPECT_SIZES[ops["aspect"]["ratio"]]
        # mittig zuschneiden auf das Seitenverhältnis, dann auf Zielgröße skalieren
        vf.append(f"crop='trunc(min(iw,ih*{w}/{h})/2)*2':'trunc(min(ih,iw*{h}/{w})/2)*2'")
        vf.append(f"scale={w}:{h}:flags=lanczos,setsar=1")
    if blur_fit:
        # Ganzes Bild zeigen, Rest mit unscharfer, abgedunkelter Kopie füllen (wie in den Referenzen)
        w, h = ASPECT_SIZES[ops["aspect"]["ratio"]]
        pre = ",".join(vf) if vf else "null"
        lines.append(f"{v}{pre},split=2[bgsrc][fgsrc]")
        lines.append(f"[bgsrc]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
                     f"gblur=sigma=40,eq=brightness=-0.08:saturation=0.8[bg]")
        lines.append(f"[fgsrc]scale={w - 2 * round(w * 0.04)}:{h}:force_original_aspect_ratio=decrease:flags=lanczos,"
                     f"setsar=1[fg]")
        lines.append("[bg][fg]overlay=(W-w)/2:(H-h)/2[vfit]")
        v, vf = "[vfit]", []
    if ass_file:
        vf.append(f"ass='{ass_file}'")
    if "fade" in ops:
        fin, fout = ops["fade"]["in"], ops["fade"]["out"]
        if fin > 0:
            vf.append(f"fade=t=in:st=0:d={fin}")
            af.append(f"afade=t=in:st=0:d={fin}")
        if fout > 0:
            st = max(0, new_duration - fout)
            vf.append(f"fade=t=out:st={st:.3f}:d={fout}")
            af.append(f"afade=t=out:st={st:.3f}:d={fout}")
    if "loudness" in ops:
        # Sprache auf Social-Media-Lautstärke bringen (Referenzen: ca. -14 LUFS)
        af.append(f"loudnorm=I={ops['loudness']['target']}:TP=-1.5:LRA=11")
        af.append(f"aresample={info.get('sample_rate') or 48000}")
    vf.append("format=yuv420p")

    lines.append(f"{v}{','.join(vf)}[vout]")
    if has_audio:
        lines.append(f"{a}{','.join(af) if af else 'anull'}[aout]")
    return ";\n".join(lines)


def _zoom_pattern(keep):
    """
    Für jeden Abschnitt: gezoomt ja/nein. Gewechselt wird nur an einem Schnitt und
    frühestens nach ZOOM_MIN_SHOT Sekunden – sonst wackelt das Bild bei vielen kleinen Schnitten.
    """
    pattern, zoomed, shot = [], False, 0.0
    for a, b in keep:
        if shot >= ZOOM_MIN_SHOT:
            zoomed, shot = not zoomed, 0.0
        pattern.append(zoomed)
        shot += b - a
    return pattern


# ---------------------------------------------------------------- Untertitel

class _TimeMap:
    """Rechnet Zeiten aus dem alten Video in Zeiten im neuen (geschnittenen, beschleunigten) Video um."""

    def __init__(self, keep, speed):
        self.pieces = []   # (alt_start, alt_ende, neu_start)
        offset = 0.0
        for a, b in keep:
            self.pieces.append((a, b, offset))
            offset += b - a
        self.speed = speed

    def map_range(self, start, end):
        """Gibt (neu_start, neu_ende) zurück – oder None, wenn der Bereich herausgeschnitten wurde."""
        for a, b, offset in self.pieces:
            s, e = max(start, a), min(end, b)
            if e - s > 0.02:
                return ((offset + s - a) / self.speed, (offset + e - a) / self.speed)
        return None


def _ass_escape(text):
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def _ass_time(t):
    t = max(0.0, t)
    h, rest = divmod(t, 3600)
    m, s = divmod(rest, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _mapped_words(transcript, timemap):
    """Alle Wörter, die im neuen Video noch vorkommen, mit neuen Zeiten (ohne Füllwörter)."""
    words = []
    for seg in transcript.get("segments", []):
        for i, w in enumerate(seg["words"]):
            mapped = timemap.map_range(w["start"], w["end"])
            if not mapped:
                continue
            clean = re.sub(r"[^\wäöüß]", "", w["word"].lower())
            if clean in FILLER_WORDS:
                continue   # Füllwörter nie als Untertitel zeigen
            words.append({"text": w["word"], "start": mapped[0], "end": mapped[1],
                          "sentence_end": w["word"].rstrip().endswith((".", "!", "?"))
                          or i == len(seg["words"]) - 1})
    return words


def _make_ass(transcript, timemap, style, width, height):
    """Erstellt eine .ass-Untertiteldatei mit Zeiten im neuen Video."""
    words = _mapped_words(transcript, timemap)

    if style == "tiktok":
        groups = _group_words(words, max_words=3, max_chars=18)
        portrait = height > width
        font_size = round(min(width * 0.085, height * 0.075))
        margin_v = round(height * (0.22 if portrait else 0.10))
        header_style = (f"Style: Default,Arial,{font_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,"
                        f"-1,0,0,0,100,100,0,0,1,{max(3, round(font_size * 0.09))},0,2,60,60,{margin_v},1")
    elif style == "referenz":
        groups = _group_words(words, max_words=2, max_chars=14)
        portrait = height > width
        font_size = round(min(width * 0.075, height * 0.065))
        margin_v = round(height * (0.30 if portrait else 0.12))
        pad = max(6, round(font_size * 0.28))
        # BorderStyle 3 = Box hinter dem Text; OutlineColour ist die Boxfarbe (fast schwarz, leicht durchsichtig)
        header_style = (f"Style: Default,{REF_FONT},{font_size},&H00FFFFFF,&H00FFFFFF,&H18161412,&H00000000,"
                        f"-1,0,0,0,100,100,1,0,3,{pad},0,2,60,60,{margin_v},1")
    else:
        groups = _group_words(words, max_words=12, max_chars=42)
        font_size = round(height * 0.045)
        header_style = (f"Style: Default,Arial,{font_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,"
                        f"0,0,0,0,100,100,0,0,1,{max(2, round(font_size * 0.06))},1,2,80,80,{round(height * 0.06)},1")

    header = (
        "[Script Info]\nScriptType: v4.00+\nWrapStyle: 0\nScaledBorderAndShadow: yes\n"
        f"PlayResX: {width}\nPlayResY: {height}\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"{header_style}\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    events = []
    for gi, group in enumerate(groups):
        # Anzeige bis kurz nach dem letzten Wort, aber nie über die nächste Gruppe hinaus
        next_start = groups[gi + 1][0]["start"] if gi + 1 < len(groups) else None
        group_end = group[-1]["end"] + 0.4
        if next_start is not None:
            group_end = min(group_end, next_start)

        if style == "referenz":
            # GROSSBUCHSTABEN, gesprochenes Wort in Koralle, Gruppe "ploppt" beim Erscheinen kurz auf
            texts = [_ass_escape(w["text"].strip().strip(",;:.!?\"„“")).upper() for w in group]
            for wi, w in enumerate(group):
                start = w["start"]
                end = group[wi + 1]["start"] if wi + 1 < len(group) else group_end
                if end - start < 0.02:
                    continue
                pop = r"{\fscx80\fscy80\t(0,90,\fscx100\fscy100)}" if wi == 0 else ""
                line = pop + " ".join(
                    ("{\\c" + REF_ACCENT + "}" + t + r"{\c&HFFFFFF&}") if j == wi and len(group) > 1 else t
                    for j, t in enumerate(texts))
                events.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,{line}")
        elif style == "tiktok":
            # Eine Zeile pro Wort: das gerade gesprochene Wort ist gelb hervorgehoben
            texts = [_ass_escape(w["text"].strip().rstrip(",;:")) for w in group]
            for wi, w in enumerate(group):
                start = w["start"]
                end = group[wi + 1]["start"] if wi + 1 < len(group) else group_end
                if end - start < 0.02:
                    continue
                line = " ".join(
                    (r"{\c&H00FFFF&}" + t + r"{\c&HFFFFFF&}") if j == wi else t
                    for j, t in enumerate(texts))
                events.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,{line}")
        else:
            text = " ".join(_ass_escape(w["text"].strip()) for w in group)
            events.append(f"Dialogue: 0,{_ass_time(group[0]['start'])},{_ass_time(group_end)},Default,,0,0,0,,{text}")

    return header + "\n".join(events) + "\n"


def _group_words(words, max_words, max_chars):
    """Teilt die Wörter in kurze Untertitel-Gruppen (neue Gruppe bei Satzende, Pause oder Länge)."""
    groups, current = [], []
    for w in words:
        if current:
            length = sum(len(x["text"]) + 1 for x in current) + len(w["text"])
            gap = w["start"] - current[-1]["end"]
            if len(current) >= max_words or length > max_chars or gap > 0.7 or current[-1]["sentence_end"]:
                groups.append(current)
                current = []
        current.append(w)
    if current:
        groups.append(current)
    return groups
