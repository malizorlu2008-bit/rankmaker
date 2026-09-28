"""pipeline/reserve/ icindeki mp4'leri kanaldaki videolarla eslestirip
reserve.json'a yazar.

Neden elle degil: yedek kutuphanesine video eklemek elle yapilan bir is
(YouTube'da indirme API'si yok; dosyalar Takeout ya da Studio uzerinden
indirilip klasore konuyor). Katalog kaydini elle yazmak hataya acik — baslik,
konu, sure, kaynak video kimligi tutmali. Bu arac dosyayi SURESINDEN eslestirip
kaydi kendisi olusturuyor.

Kullanim:
    python3 -m pipeline.reserve_catalog            # onizleme
    python3 -m pipeline.reserve_catalog --apply    # reserve.json'a yaz
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RESERVE_DIR = os.path.join(HERE, "reserve")
CATALOG_PATH = os.path.join(HERE, "state", "reserve.json")
TOKEN_PATH = os.path.join(os.path.dirname(HERE), ".credentials", "token.json")

# Baslikta gecen kelimeden konuyu cikaramadigimiz durumlar icin elle harita.
# (konu, tur, sonek) — build_tags/build_description bunlari kullaniyor.
KONU_HARITASI = {
    "live tv": ("Live TV", "ranking", "Moments"),
    "animal": ("Animal", "ranking", "Moments"),
    "pool jump": ("Pool Jump", "ranking", "Moments"),
    "first date": ("First Date", "ranking", "Fails"),
    "road trip": ("Road Trip", "ranking", "Fails"),
    "job interview": ("Job Interview", "ranking", "Fails"),
    "diy": ("DIY", "ranking", "Fails"),
}


def sureyi_saniyeye_cevir(iso):
    """PT1M12S -> 72"""
    m = re.match(r"PT(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    return int(m.group(1) or 0) * 60 + int(m.group(2) or 0)


def dosya_suresi(path):
    from pipeline import motion
    try:
        return int(round(motion.probe_duration(path) or 0))
    except Exception:
        return 0


def konu_cikar(baslik):
    """Baslikdan (konu, tur, sonek). Once elle harita, sonra kalip."""
    dusuk = baslik.lower()
    for anahtar, deger in KONU_HARITASI.items():
        if anahtar in dusuk:
            return deger
    # Once RANKING kalibi. Bu kontrol slime/satisfying kelime kontrolunden ONCE
    # olmali: "Ranking Most Satisfying Parachute Fails" bir Ranking videosu ama
    # icinde "satisfying" gectigi icin yanlis siniflaniyordu.
    m = re.search(r"(?:ranking|top \d+)\s+(?:the\s+)?\w+\s+(.+?)\s+(fails|moments)",
                  dusuk)
    if m:
        konu = m.group(1)
        # "most satisfying parachute" -> "parachute": bastaki sifatlari at
        konu = re.sub(r"^(most|best|worst|funniest|craziest|satisfying|unluckiest|wildest|epic)\s+",
                      "", konu).strip()
        return (konu.title(), "ranking", m.group(2).title())
    if "slime" in dusuk:
        return ("Slime", "satisfying", "Moments")
    if "asmr" in dusuk or "satisfying" in dusuk:
        return ("Satisfying Mix", "satisfying", "Moments")
    return (baslik, "ranking", "Fails")


def kanal_videolari():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    creds = Credentials.from_authorized_user_file(TOKEN_PATH)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    yt = build("youtube", "v3", credentials=creds)
    up = yt.channels().list(part="contentDetails", mine=True).execute()
    pid = up["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    ids, tok = [], None
    while True:
        pl = yt.playlistItems().list(part="contentDetails", playlistId=pid,
                                     maxResults=50, pageToken=tok).execute()
        ids += [i["contentDetails"]["videoId"] for i in pl["items"]]
        tok = pl.get("nextPageToken")
        if not tok:
            break
    out = []
    for i in range(0, len(ids), 50):
        for v in yt.videos().list(part="snippet,statistics,contentDetails",
                                  id=",".join(ids[i:i + 50])).execute()["items"]:
            out.append({
                "id": v["id"],
                "title": v["snippet"]["title"].strip(),
                "views": int(v["statistics"].get("viewCount", 0)),
                "seconds": sureyi_saniyeye_cevir(v["contentDetails"]["duration"]),
            })
    return out


def main(apply=False):
    katalog = json.load(open(CATALOG_PATH)) if os.path.exists(CATALOG_PATH) else {"videos": []}
    kayitli = {v["file"] for v in katalog["videos"]}
    dosyalar = sorted(f for f in os.listdir(RESERVE_DIR) if f.endswith(".mp4"))
    yeni_dosyalar = [f for f in dosyalar if f not in kayitli]
    print(f"klasorde {len(dosyalar)} mp4, katalogda {len(kayitli)}, YENI {len(yeni_dosyalar)}")
    if not yeni_dosyalar:
        return 0

    videolar = kanal_videolari()
    kullanilan_id = {v.get("source_video_id") for v in katalog["videos"]}
    eklenen = 0
    kimlikle = {v["id"]: v for v in videolar}
    for dosya in yeni_dosyalar:
        # Dosya adi video kimligiyse (indirirken oyle adlandirildiysa) sureyle
        # ugrasmaya gerek yok — ayni baslikli ve ayni uzunluktaki videolarda
        # sure eslesmesi belirsiz kaliyor.
        kok = os.path.splitext(dosya)[0]
        if kok in kimlikle and kok not in kullanilan_id:
            v = kimlikle[kok]
            konu, tur, sonek = konu_cikar(v["title"])
            katalog["videos"].append({
                "file": dosya, "title": v["title"], "views": v["views"],
                "used": False, "source_video_id": v["id"],
                "topic": konu, "kind": tur, "suffix": sonek})
            print(f'  + {v["views"]:>4} izlenme  {v["title"][:40]:<42} konu={konu}/{tur}')
            kullanilan_id.add(v["id"])
            eklenen += 1
            continue

        sure = dosya_suresi(os.path.join(RESERVE_DIR, dosya))
        adaylar = [v for v in videolar
                   if v["id"] not in kullanilan_id and abs(v["seconds"] - sure) <= 1]
        if not adaylar:
            print(f"  ESLESMEDI ({sure}sn): {dosya}")
            continue
        if len(adaylar) > 1:
            print(f"  BELIRSIZ ({sure}sn): {dosya} -> " +
                  ", ".join(f'{a["title"][:30]}' for a in adaylar))
            continue
        v = adaylar[0]
        konu, tur, sonek = konu_cikar(v["title"])
        kayit = {"file": dosya, "title": v["title"], "views": v["views"],
                 "used": False, "source_video_id": v["id"],
                 "topic": konu, "kind": tur, "suffix": sonek}
        print(f'  + {v["views"]:>4} izlenme {sure:>3}sn  {v["title"][:40]:<42} konu={konu}/{tur}')
        katalog["videos"].append(kayit)
        kullanilan_id.add(v["id"])
        eklenen += 1

    katalog["videos"].sort(key=lambda v: -(v.get("views") or 0))
    if apply and eklenen:
        json.dump(katalog, open(CATALOG_PATH, "w"), ensure_ascii=False, indent=2)
        print(f"\nreserve.json guncellendi (+{eklenen} kayit)")
    elif eklenen:
        print(f"\n(onizleme — yazmak icin --apply)")
    return eklenen


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(HERE))
    main(apply="--apply" in sys.argv)
