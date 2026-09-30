"""Günlük çalışma sonrası özet e-postası (Gmail SMTP).

GitHub Actions'ta `if: always()` ile çalışır — çalışma başarısız olsa bile
haber gelsin diye. Kimlik bilgileri repo secret'larından env ile geliyor;
kodda hiçbir şifre yok.

Bu adım asla workflow'u düşürmez: e-posta gönderilemezse hata yazdırılır ama
çıkış kodu 0 kalır (videolar zaten yüklenmiş olabilir, bildirim yüzünden
çalışma "başarısız" görünmesin).
"""
import datetime as dt
import json
import os
import smtplib
import ssl
import sys
from email.message import EmailMessage

# Alici adresi koda yazilmiyor: depo 2026-09-28'de public'e cevrildi, adres
# artik NOTIFY_TO secret'indan geliyor. Tanimli degilse bildirim atlanir.
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465


def load_summary():
    out_dir = os.environ.get("RANKMAKER_OUT_DIR") or "/tmp"
    path = os.path.join(out_dir, "summary.json")
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def format_clip(clip):
    likes = clip.get("likes")
    likes_text = f"{likes:,}".replace(",", ".") if isinstance(likes, int) else "?"
    label = clip.get("label") or "(etiketsiz)"
    return f"      - {label} ({likes_text} begeni, {clip.get('duration')}sn)"


# Bildirim saat kapisi. daily_run.reserve_after_hour/son_deneme_mi ile ayni
# desen: Istanbul saati bu degerin altindaysa mail ATILMAZ.
# Neden (kullanici karari 2026-09-30): gunde 5 tetikleyici var ve her biri
# ayri bir "video yuklendi" maili atiyordu. Kullanici gunde TEK mail istiyor,
# o da gunun SON calismasindan — o noktada uretim bitmis oluyor ama videolar
# 18:00'den once yayinlanmadigi icin bir sorun hala duzeltilebilir durumda.
ISTANBUL_OFFSET = dt.timedelta(hours=3)   # Europe/Istanbul, yil boyu UTC+3


def bildirim_zamani_mi():
    """Bu calisma gunun mail atacak calismasi mi?

    FORCE_NOTIFY=1 elle tetiklenen calismada saati beklemeden acar.
    NOTIFY_AFTER_HOUR tanimli degilse eski davranis: her calisma mail atar."""
    if os.environ.get("FORCE_NOTIFY") == "1":
        return True
    esik = os.environ.get("NOTIFY_AFTER_HOUR")
    if not esik:
        return True
    try:
        esik = int(esik)
    except ValueError:
        return True
    return (dt.datetime.now(dt.timezone.utc) + ISTANBUL_OFFSET).hour >= esik


def gunun_videolari():
    """Bugun YAYIMLANMAK UZERE yuklenmis videolar (state dosyasindan).

    Mail gunde bir kez atildigi icin o tek mail SADECE son calismayi degil
    GUNUN TAMAMINI anlatmali; sabahki calismanin urettigi videolar da
    gorunsun. Kaynak covered_topics.json, yani boru hattinin kendi kaydi."""
    yol = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "pipeline", "state", "covered_topics.json")
    bugun = (dt.datetime.now(dt.timezone.utc) + ISTANBUL_OFFSET).strftime("%Y-%m-%d")
    try:
        with open(yol) as f:
            kayit = json.load(f)
    except Exception:
        return bugun, []
    return bugun, [u for u in (kayit.get("uploads") or []) if u.get("date") == bugun]


def _gun_ozeti(lines):
    bugun, ups = gunun_videolari()
    lines.append("")
    lines.append(f"--- {bugun} GUNUN TAMAMI: {len(ups)}/4 video ---")
    if not ups:
        lines.append("  (state dosyasinda bugune ait kayit yok)")
        return
    for u in ups:
        yorum = "yorum atildi" if u.get("commented") else "yorum bekliyor"
        lines.append(f"  - {u.get('topic')}: {u.get('title')}  [{yorum}]")
    if len(ups) < 4:
        lines.append(f"  NOT: {4 - len(ups)} slot bos kaldi.")


def build_body(summary, job_status, run_url):
    lines = []
    if summary:
        lines.append(f"Tarih: {summary.get('date')}")
        if summary.get("dry_run"):
            lines.append("NOT: Bu bir TEST calismasiydi, YouTube'a yukleme yapilmadi.")
        lines.append("")

    videos = (summary or {}).get("videos") or []
    if not videos:
        if (summary or {}).get("nothing_to_do"):
            dolu = ", ".join((summary or {}).get("already_filled") or [])
            lines.append(f"Bugunun yayin saatleri ({dolu}) zaten doluydu; "
                         "bu calisma gunun ikinci denemesiydi, yapacak is yoktu.")
        elif (summary or {}).get("reserve_deferred"):
            lines.append("Hic video uretilemedi (kaynak bu sunucuda bloklu).")
            lines.append("Yedek kutuphanesi HENUZ kullanilmadi: gunun ikinci "
                         "tetikleyicisi (13:00) yeni bir sunucuda gercek video "
                         "uretmeyi deneyecek. Orada da olmazsa saatler yedekten "
                         "dolar. Yani bu kirmizi X gunun sonucu degil.")
        else:
            lines.append("Hic video uretilemedi.")
    _havuz_notu(summary, lines)
    _grup_notu(summary, lines)
    _gun_ozeti(lines)
    for v in videos:
        kind = v.get("kind")
        status = v.get("status")
        if status == "skipped":
            lines.append(f"[ATLANDI] {kind}: {v.get('reason')}")
            lines.append("")
            continue
        icon = "[YUKLENDI]" if status == "uploaded" else "[URETILDI]"
        lines.append(f"{icon} {kind}: {v.get('title')}")
        # Klipler bankadan geldiyse bunu gormek onemli: banka sessizce tukenirse
        # bir sabah hic video cikmaz. "banka" gorurseniz kaynak calismiyor.
        source = v.get("clip_source")
        if source and source != "kaynak":
            lines.append(f"   DIKKAT klip kaynagi: {source} "
                         f"(tikwm erisilemiyor, banka kullaniliyor)")
        if v.get("publish_local"):
            lines.append(f"   Yayin saati: bugun {v['publish_local']}")
        if v.get("url"):
            lines.append(f"   Link: {v['url']}")
        clips = v.get("clips") or []
        if clips:
            lines.append("   Klipler:")
            lines.extend(format_clip(c) for c in clips)
        lines.append("")

    atilan = (summary or {}).get("comments_posted")
    if atilan:
        lines.append(f"Dunku videolara {atilan} sohbet baslatici yorum atildi.")
        lines.append("")

    lines.append(f"Calisma durumu: {job_status or 'bilinmiyor'}")
    if run_url:
        lines.append(f"Detayli log: {run_url}")
    return "\n".join(lines)


def _havuz_notu(summary, lines):
    """Bos kalan slot yedekle DOLDURULMADIYSA sebebini yaz.

    2026-09-05'ten beri yedek sadece kaynak tamamen bloklandiginda aciliyor;
    havuz zayifligi yedekle kapatilmiyor (kullanici karari: bir dakikalik eski
    re-upload'lar yayinlanmasin). Bos slot artik NORMAL bir sonuc, ama sebebi
    yazilmazsa ariza gibi gorunur."""
    n = (summary or {}).get("reserve_skipped_pool_thin")
    if not n:
        return
    lines.append("")
    lines.append(f"{n} slot BOS birakildi: kaynak bloklu degil, havuzda yeterli "
                 "taze klip yoktu. Yedek kutuphanesi bilerek acilmadi — eski "
                 "formatli re-upload yayinlamak yerine slot bos kaliyor.")
    lines.append("Bu tekrar ederse konu havuzunu genislet "
                 "(python3 -m pipeline.topic_scan --adaylar).")


def _grup_notu(summary, lines):
    """Gaming slotu gaming disi bir konuyla dolduysa haber ver.

    2026-09-30 kullanici karari: gaming havuzu tam kadro veremezse slot bos
    kalmasin, gaming disi konuyla dolsun. Gunde 4 video korunuyor ama o gun 2
    yerine 1 gaming videosu cikiyor — bu sessiz kalmamali, cunku surekli
    tekrar ederse gaming havuzunun genisletilmesi gerektigini gosterir."""
    n = (summary or {}).get("group_fallback")
    if not n:
        return
    lines.append("")
    lines.append(f"{n} gaming slotu GAMING DISI konuyla dolduruldu: gaming "
                 "havuzundaki konularin hicbiri tam kadro veremedi. Video "
                 "sayisi korundu, ama bugun beklenenden az gaming videosu var.")
    lines.append("Bu tekrar ederse gaming konularini genislet/olc "
                 "(Actions -> Topic Candidate Scan).")


def build_subject(summary, job_status):
    videos = (summary or {}).get("videos") or []
    uploaded = [v for v in videos if v.get("status") == "uploaded"]
    rendered = [v for v in videos if v.get("status") == "rendered"]
    date = (summary or {}).get("date", "")

    if job_status and job_status.lower() not in ("success", ""):
        if not uploaded and (summary or {}).get("reserve_deferred"):
            return f"RankForMee: sabah kaynak bloklu - 13:00 denemesi bekleniyor ({date})"
        return f"RankForMee: calisma BASARISIZ ({date})"
    if not videos and (summary or {}).get("nothing_to_do"):
        return f"RankForMee: gunun saatleri zaten dolu, islem yok ({date})"
    atlanan = [v for v in videos if v.get("status") == "skipped"]
    if uploaded:
        # Atlanan slot konuya da yaziliyor: "4 bekliyordum 3 geldi" durumunun
        # e-postayi acmadan gorulmesi lazim. Kullanici 2026-09-05'te tam bunu
        # farketmisti ("bazen 3 oyun videosu falan atiyor").
        if atlanan:
            return (f"RankForMee: {len(uploaded)} video zamanlandi, "
                    f"{len(atlanan)} slot bos ({date})")
        return f"RankForMee: {len(uploaded)} video zamanlandi ({date})"
    if rendered:
        return f"RankForMee: {len(rendered)} video uretildi - test ({date})"
    return f"RankForMee: video yuklenemedi ({date})"


def main():
    user = os.environ.get("GMAIL_USER")
    password = os.environ.get("GMAIL_APP_PASSWORD")
    # NOTIFY_TO tanimli degilse gonderen hesabin kendisine gider. Boylece
    # kisisel adres public depoda durmuyor ama ayri bir secret da gerekmiyor.
    recipient = os.environ.get("NOTIFY_TO") or user

    if not user or not password:
        print("bildirim atlandi: GMAIL_USER / GMAIL_APP_PASSWORD tanimli degil")
        return

    if not bildirim_zamani_mi():
        print(f"bildirim atlandi: gunun son calismasi degil "
              f"(NOTIFY_AFTER_HOUR={os.environ.get('NOTIFY_AFTER_HOUR')}). "
              f"Gunluk tek ozet mail son tetikleyiciden gidiyor.")
        return

    summary = load_summary()
    job_status = os.environ.get("JOB_STATUS")
    run_url = os.environ.get("RUN_URL")

    msg = EmailMessage()
    msg["Subject"] = build_subject(summary, job_status)
    msg["From"] = user
    msg["To"] = recipient
    msg.set_content(build_body(summary, job_status, run_url))

    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ssl.create_default_context()) as smtp:
            smtp.login(user, password)
            smtp.send_message(msg)
        print(f"bildirim gonderildi: {recipient}")
    except Exception as e:
        print(f"bildirim gonderilemedi: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
