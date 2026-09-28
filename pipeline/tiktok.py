"""Headless port of app.js's TikTok fetch/download (fetchTiktokDownloadInfo)."""
import re

import requests

TIKWM_API = "https://www.tikwm.com/api/"


def is_likely_tiktok_link(link):
    return bool(re.search(r"tiktok\.com|douyin\.com", link, re.IGNORECASE))


def fetch_tiktok_download_info(link):
    res = requests.get(TIKWM_API, params={"url": link}, timeout=30)
    if not res.ok:
        raise RuntimeError("TikTok servisi yanıt vermedi")
    data = res.json()
    if data.get("code") != 0 or not data.get("data"):
        raise RuntimeError(data.get("msg") or "Video bulunamadı")
    info = data["data"]
    video_url = info.get("play") or info.get("hdplay")
    if not video_url:
        raise RuntimeError("İndirme linki alınamadı")
    return {
        "video_url": video_url,
        "title": info.get("title") or "TikTok video",
        "duration": info.get("duration"),
        "digg_count": info.get("digg_count") or 0,
    }


def download_from_info(info, dest_path):
    """Önceden fetch_tiktok_download_info ile çekilmiş meta veriyle indirir
    (ikinci bir tikwm API çağrısı yapmadan) — en çok beğenilen adayları
    seçmek için önce toplu meta veri çekilip sıralandığında kullanılır."""
    resp = requests.get(info["video_url"], timeout=60, stream=True)
    if not resp.ok:
        raise RuntimeError("Video indirilemedi")
    with open(dest_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1 << 16):
            if chunk:
                f.write(chunk)
    return {"path": dest_path, "title": info["title"], "duration": info["duration"]}


def download_tiktok_clip(link, dest_path):
    info = fetch_tiktok_download_info(link)
    return download_from_info(info, dest_path)
