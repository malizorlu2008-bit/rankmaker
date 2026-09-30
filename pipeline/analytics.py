"""Izlenme ORANI (retention) verisi — sessizce biriktirilir.

NEDEN AYRI BIR SEY: performance.py izlenme SAYISINI olcuyor (Data API). Ama
Shorts dagitimini asil belirleyen sey kac kisinin tikladigi degil, tiklayanin
videonun NE KADARINI izledigi. Bu veri sadece YouTube Analytics API'de var ve
yt-analytics.readonly izni gerektiriyor (2026-09-30'da eklendi).

Izlenme sayisina gore iki ustunlugu var:
  - daha erken: oran ilk saatlerde oturuyor, izlenme sayisi gunlerce suruyor
  - daha az gurultulu: bir videonun viral olmasi sansa bagli, izlenme orani
    icerigin kendisini olcuyor

KULLANICI KARARI 2026-09-30: bu veri SIMDILIK HICBIR YERE BAGLANMIYOR. Ne konu
secimine, ne gunluk ozet mailine, ne bir rapora. Sadece dosyaya yaziliyor.
Sebep: ayni gun konu siralamasi zaten degistirildi; ustune bir sinyal daha
eklenirse izlenme kipirdadiginda hangisinin ise yaradigi ayrilamaz. ~14 Ekim
2026'da iki haftalik veri birikince bakilacak.

BU MODUL CALISMAYI ASLA DUSURMEZ. Token'da izin yoksa, API hata verirse ya da
kota biterse: tek satir log, None doner, eski dosya korunur. Video uretimi bu
yuzden durmaz.
"""
import json
import os
import statistics
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(HERE, "state", "topic_retention.json")

# Kac gunluk pencereye bakilir. Analytics verisi 2-3 gun gecikmeli oturuyor,
# o yuzden pencere genis tutuluyor.
PENCERE_GUN = 60
# Bir konunun orani ciddiye alinmadan once kac videosu olmali
# (performance.MIN_VIDEO ile ayni gerekce).
MIN_VIDEO = 3


def oku():
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except Exception:
        return {"tarih": None, "kanal_orani": None, "konular": {}, "videolar": {}}


def oranlar():
    """{konu: izlenme orani medyani (%)}. Henuz veri yoksa bos sozluk."""
    return (oku() or {}).get("konular") or {}


def guncelle(creds, uploads):
    """Analytics'ten video bazinda izlenme oranini cekip konu medyanlarini yazar.

    creds   : google.oauth2.credentials.Credentials (daily_run zaten kuruyor)
    uploads : state["uploads"] — video_id ile konuyu eslestiren kayit

    Basarisizlikta None doner ve eski dosyaya DOKUNMAZ.
    """
    try:
        from googleapiclient.discovery import build

        vid2topic = {}
        for u in uploads or []:
            vi = (u.get("video_id") or "").replace("watch?v=", "")
            if vi and u.get("topic"):
                vid2topic[vi] = u["topic"]
        if not vid2topic:
            return None

        bugun = datetime.now(timezone.utc).date()
        ya = build("youtubeAnalytics", "v2", credentials=creds)
        cevap = ya.reports().query(
            ids="channel==MINE",
            startDate=(bugun - timedelta(days=PENCERE_GUN)).isoformat(),
            endDate=bugun.isoformat(),
            metrics="views,averageViewPercentage,averageViewDuration",
            dimensions="video",
            sort="-views",
            maxResults=200,
        ).execute()

        basliklar = [h["name"] for h in cevap.get("columnHeaders", [])]
        satirlar = cevap.get("rows") or []
        if not satirlar:
            print("  izlenme orani: Analytics henuz veri dondurmedi")
            return None

        i_vid = basliklar.index("video")
        i_oran = basliklar.index("averageViewPercentage")
        i_sure = basliklar.index("averageViewDuration")
        i_gor = basliklar.index("views")

        videolar = {}
        for r in satirlar:
            vid = r[i_vid]
            if vid not in vid2topic:
                continue          # elle yuklenen/yedek videolari sayma
            videolar[vid] = {
                "konu": vid2topic[vid],
                "oran": round(float(r[i_oran]), 1),
                "saniye": round(float(r[i_sure]), 1),
                "izlenme": int(r[i_gor]),
            }
        if not videolar:
            return None

        gruplu = {}
        for d in videolar.values():
            gruplu.setdefault(d["konu"], []).append(d["oran"])
        konular = {t: round(statistics.median(v), 1)
                   for t, v in gruplu.items() if len(v) >= MIN_VIDEO}

        veri = {
            "tarih": bugun.isoformat(),
            "pencere_gun": PENCERE_GUN,
            "kanal_orani": round(statistics.median(
                [d["oran"] for d in videolar.values()]), 1),
            "olculen_video": len(videolar),
            "konular": konular,
            "videolar": videolar,
        }
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        with open(STATE_PATH, "w") as f:
            json.dump(veri, f, indent=2, ensure_ascii=False)
        return veri

    except Exception as e:
        mesaj = str(e)
        dusuk = mesaj.lower()
        # Uc ayri arizanin uc ayri cozumu var; hepsini "izin yok" diye
        # raporlamak yanlis yere bakmaya yol aciyor (2026-09-30'da tam bunu
        # yaptim: kapsam token'da vardi ama API projede kapaliydi).
        if "has not been used in project" in dusuk or "accessnotconfigured" in dusuk:
            print("  izlenme orani atlandi: YouTube Analytics API Google Cloud "
                  "projesinde ETKIN DEGIL (console.cloud.google.com -> APIs -> "
                  "YouTube Analytics API -> Enable)")
        elif "insufficient" in dusuk or "scope" in dusuk:
            print("  izlenme orani atlandi: token'da yt-analytics.readonly izni yok "
                  "(.credentials/authorize.py yeniden calistirilmali)")
        else:
            print(f"  izlenme orani alinamadi ({mesaj[:70]}) — eski veri korunuyor")
        return None
