"""Konu basina GERCEK IZLENME performansi — konu siralamasinin ikinci sinyali.

NEDEN VAR (olcum 2026-09-30, 147 olgun video):
Konu siralamasi bugune kadar tek bir seye bakiyordu: konunun kac klip verdigi
(pipeline/state/topic_scores.json). Kanalin kendi verisiyle olculdugunde o
sayinin izlenmeyi TAHMIN ETMEDIGI gorundu — korelasyon r = +0,21. Somut hali:

  Roblox        klip skoru 53 (birinci) -> izlenme medyani  4.209
  Rocket League klip skoru 15 (sondan 3.) -> izlenme medyani 10.651
  Puppy         klip skoru 28           -> izlenme medyani 14.312
  Call Of Duty  klip skoru 25           -> izlenme medyani  1.230

Yani sistem, en cok klip verdigi icin Roblox'u one aliyor ve kanalin en iyi
videolarini ureten Rocket League'i geriye atiyordu. Konular arasi fark 12 kat.

TASARIM: klip bollugu KAPI olarak kaliyor (bol_havuz) — tam kadro veremeyen
konuyu denemek bosuna. Kapidan gecenler arasinda ise SIRALAMA izlenme
medyanina gore yapiliyor.

OLCULMEMIS KONU KILITLENMEZ: gecmisi olmayan konu kanal medyaniyla girer, yani
ortada bir yerden baslar ve kendini kanitlama sansi bulur. Aksi halde yeni
eklenen hicbir konu asla secilmez ve havuz donar.

Sadece OLGUN videolar sayilir (MIN_YAS_GUN): daha dun cikmis bir video az
izlenmis gorunur ve konuyu haksiz yere asagi ceker.
"""
import json
import os
import statistics
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(HERE, "state", "topic_views.json")

# Bir video "olgun" sayilmadan once kac gun gecmeli.
MIN_YAS_GUN = 5
# Bir konunun medyani ciddiye alinmadan once kac videosu olmali.
MIN_VIDEO = 3

# Kanal medyaninin bu oraninin ALTINDA kalan konu rotasyondan dusurulur.
# 2026-09-30 simulasyonu (14 gun x 4 video, uc tohum) su takasi gosterdi:
#   simdiki (eleme yok)      -> 33,7 farkli konu, beklenti 2.706
#   %60 esigi + tolerans 1   -> 30,7 farkli konu, beklenti 3.327  (+%23)
#   %80 esigi + tolerans 2   -> 21,0 farkli konu, beklenti 3.622
#   hepten performans        ->  8,0 farkli konu, beklenti 6.403
# Sonuncusu cazip gorunuyor ama havuzu tuketir: 2026-09-05 krizinin sebebi
# tam olarak az sayida konuyu ust uste islemekti. %60 secildi cunku
# cesitliligin neredeyse tamamini koruyup kazancin cogunu aliyor.
ZAYIF_ORANI = 0.6


def zayif_konular():
    """Kanitlanmis sekilde zayif konular: en az MIN_VIDEO olgun videosu olan
    ve medyani kanal medyaninin ZAYIF_ORANI katindan dusuk kalanlar.

    Bunlar rotasyondan DUSURULUR, yasaklanmaz: grubun tamami zayif cikarsa
    yine kullanilirlar (bkz. pick_ranking_topic). Olcumu olmayan konu asla
    zayif sayilmaz — yeni konu kendini kanitlayana kadar korunur."""
    veri = oku() or {}
    kanal = veri.get("kanal_medyani")
    konular = veri.get("konular") or {}
    if not kanal or not konular:
        return set()
    esik = kanal * ZAYIF_ORANI
    return {t for t, m in konular.items() if m < esik}


def _bos():
    return {"tarih": None, "kanal_medyani": None, "konular": {}}


def oku():
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except Exception:
        return _bos()


def medyanlar():
    """{konu: izlenme medyani}. Dosya yoksa bos sozluk."""
    return (oku() or {}).get("konular") or {}


def kanal_medyani():
    """Olculmemis konularin girecegi varsayilan deger."""
    return (oku() or {}).get("kanal_medyani")


def guncelle(yt, uploads):
    """Kanaldan izlenmeleri cekip konu medyanlarini yeniden hesaplar.

    yt      : googleapiclient YouTube servisi (daily_run zaten kuruyor)
    uploads : state["uploads"] — video_id ile konuyu eslestiren kayit

    Hata durumunda eski dosyaya DOKUNMAZ ve None doner; izlenme verisi
    alinamadi diye konu siralamasi bozulmasin.
    """
    try:
        vid2topic = {}
        for u in uploads or []:
            vi = (u.get("video_id") or "").replace("watch?v=", "")
            if vi and u.get("topic"):
                vid2topic[vi] = u["topic"]
        if not vid2topic:
            return None

        sinir = (datetime.now(timezone.utc) - timedelta(days=MIN_YAS_GUN))
        idler = list(vid2topic)
        izlenme = {}
        for i in range(0, len(idler), 50):
            r = yt.videos().list(part="snippet,statistics,status",
                                 id=",".join(idler[i:i + 50])).execute()
            for v in r.get("items", []):
                if v.get("status", {}).get("privacyStatus") != "public":
                    continue
                yayin = v["snippet"].get("publishedAt") or ""
                try:
                    t = datetime.fromisoformat(yayin.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if t > sinir:
                    continue                      # henuz olgunlasmadi
                izlenme[v["id"]] = int(v.get("statistics", {}).get("viewCount", 0))
        if not izlenme:
            return None

        gruplu = {}
        for vid, gor in izlenme.items():
            gruplu.setdefault(vid2topic[vid], []).append(gor)

        konular = {t: int(statistics.median(g))
                   for t, g in gruplu.items() if len(g) >= MIN_VIDEO}
        veri = {
            "tarih": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "kanal_medyani": int(statistics.median(list(izlenme.values()))),
            "olculen_video": len(izlenme),
            "konular": konular,
        }
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        with open(STATE_PATH, "w") as f:
            json.dump(veri, f, indent=2, ensure_ascii=False)
        return veri
    except Exception as e:
        print(f"  izlenme performansi guncellenemedi ({e}) — eski veri korunuyor")
        return None
