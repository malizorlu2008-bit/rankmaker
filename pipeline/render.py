"""Headless port of app.js's playSequence/handleExport: builds the reversed
(worst->best) play timeline, composites frames, encodes video via ffmpeg,
builds a matching trimmed/concatenated audio track, and muxes the two.
"""
import os
import subprocess
import tempfile

import cv2
from PIL import Image

from . import motion
from .compose import CANVAS_H, CANVAS_W, compose_frame
from .ffmpeg_utils import get_ffmpeg_path


def prepare_items(items, max_clip_seconds):
    """Fills duration/start/trim for items that don't have them yet, running
    motion-based action-moment detection for clips longer than the cap
    (mirrors handleTiktokFetch + autoDetectStart in app.js)."""
    for item in items:
        if not item.get("path"):
            continue
        if item.get("duration") is None:
            item["duration"] = motion.probe_duration(item["path"])
        duration = item["duration"]
        if duration and duration > max_clip_seconds and not item.get("start") and not item.get("trim"):
            # from_start: klibin BASINDAN al, hareket analizi yapma. Tatmin
            # edici/ASMR klipleri icin: o iceriklerde tek bir "aksiyon ani" yok,
            # akis bastan sona ayni. Hareket penceresi secince video ortadan
            # rastgele bir yerden basliyor ve kullanicinin 2026-08-16'da
            # bildirdigi "sacma sapan yerden kesilmis" hissi olusuyor.
            if item.get("from_start"):
                item["start"] = 0
                item["trim"] = max_clip_seconds
                continue
            start, trim, _ = motion.auto_detect_start(item["path"], max_clip_seconds, item.get("trim"))
            item["start"] = start
            if trim:
                item["trim"] = trim
    return items


# Oynatma sirasi: 2, 4, 3, 5, 1 (kullanici karari 2026-08-16). Onceki sira
# duz geri sayimdi (5,4,3,2,1); artik video 2. sirayla aciliyor ve 1. sira sona
# saklaniyor, arada sira zikzak yapiyor. Listedeki NUMARALAR degismiyor, sadece
# hangi klibin ne zaman oynatildigi degisiyor.
# Bu, items dizisindeki 0-tabanli konumlar: items[0] = 1. sira (en cok begenilen).
PLAY_ORDER = {
    5: [1, 3, 2, 4, 0],   # 2, 4, 3, 5, 1
    4: [1, 3, 2, 0],      # 2, 4, 3, 1
    3: [1, 2, 0],         # 2, 3, 1
}


def play_order(n):
    """n klip icin oynatma sirasi. Tanimli degilse eski davranis (geri sayim)."""
    sira = PLAY_ORDER.get(n)
    if sira and sorted(sira) == list(range(n)):
        return sira
    return list(range(n - 1, -1, -1))


def _build_segments(items, max_clip_seconds):
    """Oynatma sirasi PLAY_ORDER'a gore (bkz. yukarisi)."""
    order = play_order(len(items))
    segments = []
    for i in order:
        item = items[i]
        if not item.get("path"):
            continue
        trim = item.get("trim") or max_clip_seconds
        start = item.get("start", 0) or 0
        duration = item.get("duration")
        remaining = (duration - start) if duration else trim
        play_duration = min(trim, remaining) if remaining and remaining > 0 else trim
        if play_duration <= 0:
            continue
        segments.append({
            "index": i,
            "path": item["path"],
            "start": start,
            "play_duration": play_duration,
            "mute": item.get("mute", False),
        })
    return segments


def render_video_track(items, title_cfg, segments, fps, out_path):
    total_duration = sum(s["play_duration"] for s in segments)
    total_frames = max(1, round(total_duration * fps))

    cmd = [
        get_ffmpeg_path(), "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{CANVAS_W}x{CANVAS_H}", "-r", str(fps),
        "-i", "-",
        "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-preset", "veryfast", "-crf", "20",
        out_path,
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    cumulative = 0.0
    seg_idx = 0
    cap = None
    cap_path = None
    # O ana kadar oynatilmis siralar; liste bunlara gore aciliyor.
    acilanlar = set()
    try:
        for frame_no in range(total_frames):
            t_out = frame_no / fps
            while seg_idx < len(segments) - 1 and t_out >= cumulative + segments[seg_idx]["play_duration"]:
                cumulative += segments[seg_idx]["play_duration"]
                seg_idx += 1
            seg = segments[seg_idx]
            current_index = seg["index"]
            acilanlar.add(current_index)
            local_t = t_out - cumulative
            src_t = seg["start"] + local_t

            if cap_path != seg["path"]:
                if cap is not None:
                    cap.release()
                cap = cv2.VideoCapture(seg["path"])
                cap_path = seg["path"]
            cap.set(cv2.CAP_PROP_POS_MSEC, src_t * 1000.0)
            ok, frame = cap.read()
            video_frame_pil = None
            if ok:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                video_frame_pil = Image.fromarray(rgb)

            img = compose_frame(items, title_cfg, current_index, True, video_frame_pil,
                                acilanlar=acilanlar)
            proc.stdin.write(img.convert("RGB").tobytes())
    finally:
        if cap is not None:
            cap.release()
        proc.stdin.close()
        stderr = proc.stderr.read().decode(errors="ignore")
        ret = proc.wait()
        if ret != 0:
            raise RuntimeError(f"ffmpeg video encode başarısız:\n{stderr[-2000:]}")

    return total_duration


def render_audio_track(segments, out_path):
    inputs = []
    filter_parts = []
    for n, seg in enumerate(segments):
        inputs += ["-i", seg["path"]]
        end = seg["start"] + seg["play_duration"]
        mute_filter = ",volume=0" if seg.get("mute") else ""
        filter_parts.append(
            f"[{n}:a]atrim=start={seg['start']:.3f}:end={end:.3f},asetpts=PTS-STARTPTS{mute_filter}[a{n}]"
        )
    concat_inputs = "".join(f"[a{n}]" for n in range(len(segments)))
    filter_complex = ";".join(filter_parts) + f";{concat_inputs}concat=n={len(segments)}:v=0:a=1[aout]"

    cmd = [
        get_ffmpeg_path(), "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[aout]",
        "-c:a", "aac",
        out_path,
    ]
    proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg audio encode başarısız:\n{proc.stderr.decode(errors='ignore')[-2000:]}")


def mux(video_path, audio_path, out_path):
    cmd = [
        get_ffmpeg_path(), "-y",
        "-i", video_path, "-i", audio_path,
        "-c:v", "copy", "-c:a", "aac",
        "-shortest",
        out_path,
    ]
    proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg mux başarısız:\n{proc.stderr.decode(errors='ignore')[-2000:]}")


def render(items, title_cfg, out_path, max_clip_seconds=15, fps=30, workdir=None):
    prepare_items(items, max_clip_seconds)
    segments = _build_segments(items, max_clip_seconds)
    if not segments:
        raise RuntimeError("Render edilecek klip yok")

    tmp_dir = workdir or tempfile.mkdtemp(prefix="rankmaker_render_")
    tmp_video = os.path.join(tmp_dir, "video_only.mp4")
    tmp_audio = os.path.join(tmp_dir, "audio_only.m4a")

    render_video_track(items, title_cfg, segments, fps, tmp_video)
    render_audio_track(segments, tmp_audio)
    mux(tmp_video, tmp_audio, out_path)
    return out_path


def _cli():
    import argparse
    import json

    parser = argparse.ArgumentParser(description="RankMaker headless render (JSON spec -> mp4)")
    parser.add_argument("spec", help="JSON dosya yolu: {items:[...], title:{...}, max_clip_seconds, fps, out_path}")
    args = parser.parse_args()

    with open(args.spec) as f:
        spec = json.load(f)

    out = render(
        spec["items"],
        spec["title"],
        spec["out_path"],
        max_clip_seconds=spec.get("max_clip_seconds", 15),
        fps=spec.get("fps", 30),
    )
    print(json.dumps({"out_path": out}))


if __name__ == "__main__":
    _cli()
