"""Yayina girmis videolara bekleyen yorumu atar. Hizli calisir (klip/render yok).

Neden ayri: yorumlar eskiden bir sonraki gunun ilk calismasinda atiliyordu,
cunku video yuklenirken henuz GIZLI oluyor ve gizli videoya yorum atmanin
anlami yok. Kullanici karari 2026-08-17: yorum, video yayina girdikten hemen
sonra atilsin (19:10 ve 20:10 tetikleyicileri).

Kural: kayitli sorusu olan ve videosu ARTIK HERKESE ACIK olan her video.
Tarihe degil videonun gercek durumuna bakiyor — yayin saati elle degistirilmis
olabilir ya da video silinmis olabilir.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from pipeline import comment  # noqa: E402

STATE_PATH = os.path.join(HERE, "state", "covered_topics.json")
TOKEN_PATH = os.path.join(os.path.dirname(HERE), ".credentials", "token.json")
MAX_DENEME = 3


def video_durumlari(video_ids):
    """{video_id: privacyStatus}. Listede olmayan video SILINMIS demektir."""
    if not video_ids:
        return {}
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    creds = Credentials.from_authorized_user_file(TOKEN_PATH)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    yt = build("youtube", "v3", credentials=creds)
    out = {}
    ids = list(video_ids)
    for i in range(0, len(ids), 50):
        for v in yt.videos().list(part="status", id=",".join(ids[i:i + 50])).execute()["items"]:
            out[v["id"]] = v["status"]["privacyStatus"]
    return out


def video_kimligi(kayit):
    ham = (kayit.get("video_id") or "").strip()
    if "v=" in ham:
        ham = ham.split("v=")[-1]
    return ham.split("&")[0]


def main():
    with open(STATE_PATH) as f:
        state = json.load(f)

    bekleyen = [u for u in state.get("uploads", [])
                if (u.get("question") or "")
                and not u.get("commented")
                and u.get("comment_tries", 0) < MAX_DENEME
                and video_kimligi(u)]
    if not bekleyen:
        print("bekleyen yorum yok")
        return 0

    durumlar = video_durumlari({video_kimligi(u) for u in bekleyen})
    atilan = 0
    for u in bekleyen:
        vid = video_kimligi(u)
        durum = durumlar.get(vid)
        if durum is None:
            u["commented"] = True
            u["note"] = "video silinmis, yorum atilmadi"
            print(f"  {vid}: video yok, atlandi")
            continue
        if durum != "public":
            print(f"  {vid}: henuz yayinda degil ({durum}), sonraki calismaya kaldi")
            continue
        if comment.yorum_yaz(vid, u["question"]):
            u["commented"] = True
            atilan += 1
        else:
            u["comment_tries"] = u.get("comment_tries", 0) + 1

    with open(STATE_PATH, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    print(f"{atilan} yorum atildi")
    return atilan


if __name__ == "__main__":
    main()
