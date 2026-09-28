"""Yedek video kutuphanesi — bir gun hic video uretilemezse yayinlanacak hazir videolar.

Kullanici karari 2026-08-13: "bir gun video yuklenemezse eski videolardan birini
yukle". Kanaldaki eski videolar (hepsi 100 izlenmenin altinda) yeniden
degerlendirilecek; en cok tutandan baslanacak.

NEDEN DOSYALARI BIZ INDIRMIYORUZ: YouTube Data API'de kendi videonu indirme uc
noktasi YOK. Studio'daki "Indir" sadece arayuzde var. Tek otomatik yol yt-dlp
gibi araclar ve onlar YouTube'un kullanim sartlarina aykiri — bu yuzden indirme
adimi ELLE yapiliyor: kullanici Studio'dan indirir, dosyayi pipeline/reserve/
icine koyar, `python3 -m pipeline.reserve import` calistirir.

Dosya duzeni:
  pipeline/reserve/<dosya>.mp4     -> hazir videolar (repoda)
  pipeline/state/reserve.json      -> katalog: baslik, aciklama, etiket,
                                      kaynak video kimligi, izlenme, dosya

Katalog izlenmeye gore SIRALI tutuluyor (en cok tutan once) — kullanici istegi.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
RESERVE_DIR = os.path.join(HERE, "reserve")
CATALOG_PATH = os.path.join(HERE, "state", "reserve.json")


def _load():
    if not os.path.exists(CATALOG_PATH):
        return {"videos": []}
    try:
        with open(CATALOG_PATH) as f:
            data = json.load(f)
    except Exception:
        return {"videos": []}
    return data if isinstance(data, dict) else {"videos": []}


def _save(data):
    os.makedirs(os.path.dirname(CATALOG_PATH), exist_ok=True)
    with open(CATALOG_PATH, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _path(entry):
    return os.path.join(RESERVE_DIR, entry["file"]) if entry.get("file") else None


def hazir_olanlar():
    """Dosyasi gercekten duran kayitlar, en cok izlenenden aza dogru."""
    hazir = []
    for e in _load().get("videos", []):
        p = _path(e)
        if p and os.path.exists(p) and not e.get("used"):
            hazir.append(e)
    return sorted(hazir, key=lambda e: e.get("views") or 0, reverse=True)


def durum():
    videos = _load().get("videos", [])
    hazir = hazir_olanlar()
    return {
        "katalog": len(videos),
        "dosyasi_hazir": len(hazir),
        "kullanilmis": sum(1 for e in videos if e.get("used")),
    }


def en_iyisini_sec():
    """Yayinlanacak en iyi yedegi dondurur. ISARETLEMEZ.

    (yol, kayit) doner; hazir yedek yoksa (None, None). Isaretleme ayri
    (kullanildi_isaretle) cunku yukleme BASARILI olmadan isaretlemek, basarisiz
    bir denemede yedegi sessizce yakiyordu — kayit katalogdan dusuyor ama video
    kanala hic cikmiyordu."""
    hazir = hazir_olanlar()
    if not hazir:
        return None, None
    return _path(hazir[0]), hazir[0]


def kullanildi_isaretle(kayit):
    """Yukleme basarili olduktan SONRA cagrilir."""
    data = _load()
    for e in data.get("videos", []):
        if e.get("file") == kayit.get("file"):
            e["used"] = True
    _save(data)


def iceri_al():
    """pipeline/reserve/ icindeki dosyalari katalogla eslestirir.

    Eslestirme dosya adina gore: kullanici Studio'dan indirdigi dosyayi
    katalogdaki "file" alanindaki adla kaydetmeli (ya da bu fonksiyon
    eslesmeyenleri listeler)."""
    data = _load()
    isimler = {e.get("file"): e for e in data.get("videos", []) if e.get("file")}
    if not os.path.isdir(RESERVE_DIR):
        return {"eslesen": 0, "eslesmeyen": []}
    eslesen, eslesmeyen = 0, []
    for ad in sorted(os.listdir(RESERVE_DIR)):
        if not ad.lower().endswith(".mp4"):
            continue
        if ad in isimler:
            eslesen += 1
        else:
            eslesmeyen.append(ad)
    return {"eslesen": eslesen, "eslesmeyen": eslesmeyen}


if __name__ == "__main__":
    import sys
    komut = sys.argv[1] if len(sys.argv) > 1 else "durum"
    if komut == "import":
        sonuc = iceri_al()
        print(f"katalogla eslesen dosya: {sonuc['eslesen']}")
        if sonuc["eslesmeyen"]:
            print("katalogda karsiligi olmayan dosyalar:")
            for ad in sonuc["eslesmeyen"]:
                print(f"  {ad}")
    else:
        print(durum())
        for e in hazir_olanlar():
            print(f"  {e.get('views'):>4} izlenme  {e.get('title')}")
