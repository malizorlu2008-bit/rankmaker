"""Yayinlanmis bir videoya kanalin kendi adiyla yorum yazar.

Neden: Shorts'ta dagitimi besleyen en guclu sinyallerden biri yorum altina
gelen cevaplar. Sıralama videolari bunun icin ideal, cunku insanlarin refleksi
siralamaya itiraz etmek. "Abone olun" tarzi bir cagri yerine videonun KENDI
kliplerine ozel bir soru soruluyor (soruyu Gemini uretiyor, bkz. labeler.py).

DIKKAT — iki sinir:
1. Yorum SABITLENEMIYOR. YouTube Data API'sinde pin uc noktasi yok; sabitlemek
   isteniyorsa Studio'dan elle yapilmali.
2. Video yayinlanmadan yorum atmanin anlami yok. Gunluk akis videolari GIZLI
   yukleyip 19:00/20:00'a zamanliyor, o yuzden yorumlar bir SONRAKI calismada,
   yani video artik herkese acikken atiliyor (bkz. daily_run.yorumlari_gonder).

visibility.py gibi bu modul de asla hata firlatmaz: izin yoksa ya da cagri
basarisiz olursa False doner, gunluk akis etkilenmez.
"""
import json
import os
import re

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN_PATH = os.path.join(HERE, ".credentials", "token.json")
GEREKEN_IZIN = "https://www.googleapis.com/auth/youtube.force-ssl"
MAX_UZUNLUK = 200


def izin_var_mi():
    try:
        with open(TOKEN_PATH) as f:
            return GEREKEN_IZIN in (json.load(f).get("scopes") or [])
    except Exception:
        return False


def _servis():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    creds = Credentials.from_authorized_user_file(TOKEN_PATH)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
    return build("youtube", "v3", credentials=creds)


def temizle(metin):
    """Tek satir, makul uzunlukta, hashtag'siz.

    '#' HARFTEN once geliyorsa hashtag'dir, silinir; RAKAMDAN once geliyorsa
    siralama ifadesidir ve KALIR. Ilk denemede kor bir replace("#","") "Which
    raccoon deserved #1?" cumlesini "deserved 1?" yapmisti."""
    tek_satir = " ".join(str(metin or "").split())
    tek_satir = re.sub(r"#(?=[^\W\d_])", "", tek_satir)
    if len(tek_satir) > MAX_UZUNLUK:
        tek_satir = tek_satir[:MAX_UZUNLUK].rsplit(" ", 1)[0]
    return tek_satir.strip()


def yorum_yaz(video_id, metin):
    """Videoya yorum ekler. Basarili olursa True."""
    metin = temizle(metin)
    if not video_id or not metin:
        return False
    if not izin_var_mi():
        print("  yorum yazilamadi: token'da youtube.force-ssl izni yok — "
              ".credentials/authorize.py yeniden calistirilmali")
        return False
    try:
        youtube = _servis()
        youtube.commentThreads().insert(
            part="snippet",
            body={"snippet": {
                "videoId": video_id,
                "topLevelComment": {"snippet": {"textOriginal": metin}},
            }},
        ).execute()
        print(f"  yorum yazildi ({video_id}): {metin}")
        return True
    except Exception as e:
        print(f"  yorum yazilamadi ({video_id}): {e}")
        return False


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("kullanim: python3 -m pipeline.comment <video_id> <metin>")
        raise SystemExit(2)
    ok = yorum_yaz(sys.argv[1], " ".join(sys.argv[2:]))
    raise SystemExit(0 if ok else 1)
