import os
from google_auth_oauthlib.flow import InstalledAppFlow

# youtube.upload TEK BASINA yetmiyor: yedek video yayinlandiginda kanaldaki
# eskisini GIZLEMEK icin videos.update cagriliyor ve o daha genis izin istiyor
# (kullanici karari 2026-08-13). youtube.force-ssl, video guncellemeyi kapsar.
# Bu dosya degistiginde token yeniden alinmali, yoksa eski token yeni izne
# sahip olmaz ve gizleme "insufficient permissions" hatasi verir.
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]
HERE = os.path.dirname(os.path.abspath(__file__))

flow = InstalledAppFlow.from_client_secrets_file(
    os.path.join(HERE, "client_secret.json"), SCOPES
)
creds = flow.run_local_server(port=0, prompt="consent")

with open(os.path.join(HERE, "token.json"), "w") as f:
    f.write(creds.to_json())

print("OK: token saved")
