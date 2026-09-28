"""Headless port of app.js's motion/action-moment detection (analyzeMotion,
findSceneCuts, bestMotionWindow). Constants and logic are kept identical to
the browser version — they were hand-tuned against real fail clips, see
CLAUDE memory `feedback-rankmaker-workflow`.
"""
import cv2
import numpy as np

MOTION_SAMPLE_STEP = 0.3
SAMPLE_W = 64
SAMPLE_H = 114
CUT_HIST_THRESHOLD = 0.45


def _hist_of(frame_small):
    c0 = frame_small[:, :, 0].astype(np.int32) >> 6
    c1 = frame_small[:, :, 1].astype(np.int32) >> 6
    c2 = frame_small[:, :, 2].astype(np.int32) >> 6
    idx = c0 * 16 + c1 * 4 + c2
    bins = np.bincount(idx.ravel(), minlength=64).astype(np.float64)
    bins /= frame_small.shape[0] * frame_small.shape[1]
    return bins


def analyze_motion(path, progress_cb=None):
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise RuntimeError(f"Video okunamadı: {path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 0
    frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    duration = frame_count / fps if fps else 0
    if not duration or duration <= 0:
        cap.release()
        raise RuntimeError("Klip süresi okunamadı")

    samples = []
    prev = None
    prev_hist = None

    t = 0.0
    while t < duration:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
        ok, frame = cap.read()
        if not ok:
            t += MOTION_SAMPLE_STEP
            continue
        small = cv2.resize(frame, (SAMPLE_W, SAMPLE_H), interpolation=cv2.INTER_LINEAR)
        hist = _hist_of(small)

        motion = 0.0
        hist_diff = 0.0
        if prev is not None:
            diff = np.abs(small.astype(np.int32) - prev.astype(np.int32))
            motion = float(diff.sum()) / (SAMPLE_W * SAMPLE_H)
            hist_diff = float(np.abs(hist - prev_hist).sum())

        samples.append({"t": t, "motion": motion, "histDiff": hist_diff})
        prev = small
        prev_hist = hist
        if progress_cb:
            progress_cb(t / duration)
        t += MOTION_SAMPLE_STEP

    cap.release()
    return duration, samples


def find_scene_cuts(samples):
    cuts = []
    for i in range(1, len(samples) - 1):
        d = samples[i]["histDiff"] or 0
        if d < CUT_HIST_THRESHOLD:
            continue
        if d >= (samples[i - 1]["histDiff"] or 0) and d >= (samples[i + 1]["histDiff"] or 0):
            if not cuts or samples[i]["t"] - cuts[-1] > 1.0:
                cuts.append(samples[i]["t"])
    return cuts


def best_motion_window(samples, duration, window_sec):
    L = min(window_sec, duration)

    # Klip pencereden çok uzun değilse SONU al (fail anı genelde klibin sonunda).
    if duration <= L * 1.7:
        return {"start": max(0.0, duration - L), "length": L}

    cuts = find_scene_cuts(samples)

    bounds = [0.0] + cuts + [duration]
    raw_segments = [{"from": bounds[i], "to": bounds[i + 1]} for i in range(len(bounds) - 1)]

    # Çok kısa segmentleri ele (POV/hızlı kamera hareketi yanlış kesme sayılmasın).
    min_segment = min(max(4.0, L * 0.5), duration)
    segments = [s for s in raw_segments if s["to"] - s["from"] >= min_segment]
    if not segments:
        segments = [{"from": 0.0, "to": duration}]

    cut_times = {f"{c:.2f}" for c in cuts}

    chosen = None
    for seg in segments:
        seg_len = seg["to"] - seg["from"]
        peak = {"t": seg["from"], "m": -1.0}
        for s in samples:
            if s["t"] < seg["from"] + 0.35 or s["t"] > seg["to"] - 0.05:
                continue
            if f"{s['t']:.2f}" in cut_times:
                continue
            if s["motion"] > peak["m"]:
                peak = {"t": s["t"], "m": s["motion"]}
        if peak["m"] < 0:
            continue
        # Tepe kadar güçlü ama daha geç gelen bir an varsa onu tercih et.
        for s in samples:
            if s["t"] <= peak["t"]:
                continue
            if s["t"] < seg["from"] + 0.35 or s["t"] > seg["to"] - 0.05:
                continue
            if f"{s['t']:.2f}" in cut_times:
                continue
            if s["motion"] >= peak["m"] * 0.72:
                peak = {"t": s["t"], "m": s["motion"]}
        score = peak["m"] * min(1.0, seg_len / L)
        if chosen is None or score > chosen["score"]:
            chosen = {"seg": seg, "peak": peak, "score": score, "winLen": min(L, seg_len)}

    if chosen is None:
        return {"start": 0.0, "length": L}

    seg = chosen["seg"]
    peak = chosen["peak"]
    win_len = chosen["winLen"]
    tail = min(win_len * 0.3, 2.5)
    start = peak["t"] - (win_len - tail)
    start = min(start, seg["to"] - win_len)
    start = max(start, seg["from"], 0.0)

    result = {"start": start, "length": win_len}
    return _prefer_late_peak_if_missed(samples, duration, L, result)


def _prefer_late_peak_if_missed(samples, duration, window_len, result):
    """Safety net for compilation-style clips where the real payoff is a short
    final scene (often < min_segment above and so filtered out of `segments`
    entirely — e.g. a quick cut to a 3-4s reaction/punchline shot). Doesn't
    touch the segment filter itself (that guards against POV/handheld clips
    getting mis-split — see CLAUDE memory feedback-rankmaker-workflow), it
    only overrides the final pick if a strong, uncovered late spike exists."""
    if not samples:
        return result

    global_max = max((s["motion"] for s in samples), default=0.0)
    if global_max <= 0:
        return result

    tail_span = min(window_len, duration * 0.4)
    tail_from = duration - tail_span

    tail_peak = None
    for s in samples:
        if s["t"] < tail_from or s["t"] > duration - 0.1:
            continue
        if tail_peak is None or s["motion"] > tail_peak["motion"]:
            tail_peak = s

    if tail_peak is None or tail_peak["motion"] < global_max * 0.6:
        return result

    chosen_end = result["start"] + result["length"]
    if chosen_end >= tail_peak["t"] - 0.5:
        return result  # seçilen pencere zaten bu anı kapsıyor

    win_len = window_len
    tail = min(win_len * 0.3, 2.5)
    start = tail_peak["t"] - (win_len - tail)
    start = min(start, duration - win_len)
    start = max(start, 0.0)
    return {"start": start, "length": win_len}


def probe_duration(path):
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise RuntimeError(f"Video okunamadı: {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 0
    frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    cap.release()
    return frame_count / fps if fps else 0


def auto_detect_start(path, max_clip_seconds, trim=None):
    """Returns (start, trim, duration) — trim is None if the full window fit."""
    duration, samples = analyze_motion(path)
    window_sec = trim or min(max_clip_seconds, duration)
    result = best_motion_window(samples, duration, window_sec)
    start = round(result["start"] * 10) / 10
    length = result["length"]
    new_trim = trim
    if length and length < window_sec - 0.4:
        new_trim = round(length * 10) / 10
    return start, new_trim, duration
