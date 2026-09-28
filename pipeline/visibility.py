"""Kanaldaki bir videonun gorunurlugunu degistirir.

Neden ayri dosya: yedek video yayinlandiginda kanalda ayni videonun eskisi
kaliyor ve kopya olusuyordu. Kullanici karari 2026-08-13: eskisi otomatik
gizlensin.

DIKKAT: bu, .credentials/token.json'in youtube.force-ssl iznine sahip olmasini
gerektirir. Izin yoksa fonksiyon HATA FIRLATMAZ, sadece False doner ve sebebini
yazar — gizleme yapilamadi diye o gunun videosu bosa gitmesin.
"""
import json
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN_PATH = os.path.join(HERE, ".credentials", "token.json")
GEREKEN_IZIN = "https://www.googleapis.com/auth/youtube.force-ssl"


def izin_var_mi():
    try:
        with open(TOKEN_PATH) as f:
            return GEREKEN_IZIN in (json.load(f).get("scopes") or [])
    except Exception:
        return False


def gizle(video_id):
    """Videoyu 'private' yapar. Basarili olursa True."""
    if not video_id:
        return False
    if not izin_var_mi():
        print("  gizlenemedi: token'da youtube.force-ssl izni yok — "
              ".credentials/authorize.py yeniden calistirilmali")
        return False
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build

        creds = Credentials.from_authorized_user_file(TOKEN_PATH)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(TOKEN_PATH, "w") as f:
                f.write(creds.to_json())
        youtube = build("youtube", "v3", credentials=creds)
        youtube.videos().update(
            part="status",
            body={"id": video_id, "status": {"privacyStatus": "private"}},
        ).execute()
        print(f"  kanaldaki eski video gizlendi: {video_id}")
        return True
    except Exception as e:
        print(f"  gizlenemedi ({video_id}): {e}")
        return False
