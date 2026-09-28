"""Klip bankasi — kaynak (tikwm) erisilemedigi gunlerde bile GERCEK video ciksin.

Neden var: kaynak GitHub'in bazi sunucularini blokluyor ve o gunler video
uretilemiyor; yedek kutuphanesi (reserve.py) devreye giriyor ama o kutuphane
elle stoklaniyor, kendini yenilemiyor ve tukeniyor. Banka ise kaynagin CALISTIGI
gunlerde ihtiyactan fazla indirilen klipleri saklayarak kendini yeniliyor.

KULLANICI KURALI (2026-08-15) — bankanin ilk surumunde iki sikayet olmustu:
klipler baslikla alakasizdi ve video 5 yerine 3 klipe dusuyordu. Bu yuzden:

  1. Her kayit HANGI KONUDAN geldigini tasir; banka videosu TEK konudan
     doldurulur. Kedi + rakun + su kaydiragi ayni videoya asla girmez.
  2. Banka bir video ancak TEK basina TAM kadroyu (CLIP_COUNT) dolduruyorsa
     uretir. Eksikse hic uretmez — 3 kliplik ince video cikmasindansa o slot
     yedek kutuphaneden dolar.
  3. Baslik konuyla uyusun diye kayit title_topic/suffix de tasir; boylece
     banka videosunun basligi normal uretimdekiyle ayni kuraldan gecer.

Kayitlar klibin ACIKLAMASINI ve EN BEGENILEN YORUMLARINI da saklar. Sebep:
etiketleri Gemini uretiyor ve girdisi bunlar; kaynagin bloklu oldugu bir gunde
bu bilgiyi sonradan cekmek mumkun degil, banka gunu videosu etiketsiz kalirdi.

Dosya duzeni:
  pipeline/state/clip_bank.json  -> manifest (kucuk JSON, repoya commit edilir)
  pipeline/bank/<video_id>.mp4   -> klip dosyalari (Actions CACHE'inde tasinir)

Klipler neden artik repoda degil (2026-09-28): depo public'e cevrildi (Actions
dakikasi public repoda ucretsiz) ve ucuncu kisilerin TikTok klipleri herkese
acik bir depoda barinmasin diye actions/cache'e tasindi. Manifest commit
edilmeye devam ediyor; cache iskalarsa prune_missing() dosyasi olmayan
kayitlari temizler, yani banka kendini onarir ve uretim etkilenmez.

KABUL EDILEN TAKAS: kullanicinin Mac'inde toplanan bir klip artik CI'a
tasinmiyor (cache'e disaridan yazilamiyor). Banka zaten sadece kaynagin bloklu
oldugu gunler icin bir tampon; son care olan yedek kutuphanesi (reserve.py)
kullanicinin kendi videolari oldugu icin repoda kaliyor.

BILINEN TAKAS: kaynak cok uzun sure gelmezse banka tukenir. Banka sorunu
geciktirir, sonsuza kadar cozmez.
"""
import json
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
BANK_DIR = os.path.join(HERE, "bank")
MANIFEST_PATH = os.path.join(HERE, "state", "clip_bank.json")

# Tur basina saklanacak klip sayisi ust siniri.
BANK_TARGET = 20


def topup_for(kind, clip_count, topic=None):
    """Bu calismada bankaya kac fazladan klip cekilmeli.

    TAM BIR YEDEK KADRO kadar (yani videonun klip sayisi kadar). Neden: banka
    videoyu tek konudan ve tam kadro doldurmak zorunda (asagidaki kurallar), ama
    Ranking konusu her gun degisiyor. Calisma basina 3 klip yatirsaydik hicbir
    konu tek basina 5'e ulasamaz ve banka hicbir zaman video uretemezdi —
    kullanici bunu fark etti (2026-08-15). Tek bir basarili gun, o konudan
    bastan sona bir video kuracak kadar klip biriktirsin.

    Bedeli: her ekstra klip bir indirme + OpenCV kalite kontrolu demek, yani
    calisma birkac dakika uzuyor (timeout 90 dk, bol pay var).

    2026-09-05: yatirim artik SADECE BOL KONULARDAN yapiliyor (bkz. topic
    parametresi). Gunde iki videodan dorde cikilinca bu fonksiyon havuzu iki
    katti hizla tuketir hale gelmisti: her video icin 5 yerine 10 klip
    cekiliyordu. Olcum: 451 klip harcandi, konu skorlari iki haftada yariya
    indi (Horse 71->40) ve sekiz konu 5 klip esiginin altina dustu. Zayif bir
    konudan bankaya klip almak, o konunun BUGUNKU videosunu oldurmek demek."""
    if topic is not None and yeterince_bol_degil(topic, clip_count):
        return 0
    return max(0, min(clip_count, room_for(kind)))


# Bir konudan bankaya klip alabilmek icin son olculen veriminin en az bu kati
# olmasi gerekiyor. 2 = "videonun ihtiyacinin iki kati klip veriyorsa fazlasi
# gercekten fazladir".
BOLLUK_KATI = 2


def yeterince_bol_degil(topic, clip_count):
    """Konu bankaya klip verecek kadar bol degilse True.

    Skor dosyasi yoksa/konu olculmemisse yatirim YAPILMAZ (muhafazakar taraf:
    bilmiyorsak havuzu tuketmeyelim)."""
    try:
        from pipeline import topic_scan
        skor = topic_scan.skorlar().get(topic)
    except Exception:
        return True
    if skor is None:
        return True
    return skor < clip_count * BOLLUK_KATI


def _load():
    if not os.path.exists(MANIFEST_PATH):
        return {}
    try:
        with open(MANIFEST_PATH) as f:
            data = json.load(f)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _save(data):
    os.makedirs(os.path.dirname(MANIFEST_PATH), exist_ok=True)
    with open(MANIFEST_PATH, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _entry_path(entry):
    return os.path.join(BANK_DIR, entry["file"])


def load_bank(kind):
    """Dosyasi GERCEKTEN duran kayitlar. Manifest ile disk ayrisabiliyor."""
    entries = _load().get(kind) or []
    return [e for e in entries if e.get("file") and os.path.exists(_entry_path(e))]


def prune_missing():
    """Manifest'i diskle hizalar, dusen kayit sayisini doner.

    2026-08-13'te manifest yerel bir testten commit'lenmisti ve CI'da olmayan
    mp4'lere isaret ediyordu; banka dolu sanildi, video atlandi."""
    data = _load()
    dropped = 0
    for kind, entries in list(data.items()):
        kept = [e for e in (entries or []) if e.get("file") and os.path.exists(_entry_path(e))]
        dropped += len(entries or []) - len(kept)
        data[kind] = kept
    if dropped:
        _save(data)
    return dropped


def counts_by_topic(kind):
    """{konu: klip sayisi} — ozet e-postasinda ve loglarda gorunur olsun."""
    sayac = {}
    for e in load_bank(kind):
        t = e.get("topic")
        if t:
            sayac[t] = sayac.get(t, 0) + 1
    return dict(sorted(sayac.items(), key=lambda x: -x[1]))


def room_for(kind):
    return max(0, BANK_TARGET - len(load_bank(kind)))


def group_of(kind, topic):
    """Bankadaki bir konunun grubu ("gaming" ya da None).

    Kayittaki `group` alanina bakilir; ESKI kayitlarda o alan yok (2026-09-05'ten
    once yazilanlar). O durumda ad uzerinden RANKING_TOPICS'e bakilir, orada da
    yoksa GAMING DISI sayilir — emekliye ayrilmis konularin hepsi gaming disiydi
    ('Horse Fail', 'Gym Fail' gibi). Yanlis tarafa dusmesi gerekiyorsa gaming
    disina dussun: gaming slotuna sizmasi, gaming disi slota sizmasindan daha
    kotu (kullanici gunde 3 gaming videosu gordu, tersini degil).
    """
    for e in load_bank(kind):
        if e.get("topic") == topic and e.get("group"):
            return e["group"]
    try:
        from pipeline.daily_run import RANKING_TOPICS
        for t in RANKING_TOPICS:
            if t["topic"] == topic:
                return t.get("group")
    except Exception:
        pass
    return None


def topics_with_enough(kind, need, group="__hepsi__"):
    """`need` kadar klibi TEK BASINA olan konular, en dolusu once.

    group verilirse (None dahil!) sadece o gruptaki konular doner. Varsayilan
    "__hepsi__" suzgec yok demek — None'in kendisi gecerli bir grup degeri
    oldugu icin varsayilan olarak kullanilamiyor.

    Bu suzgec 2026-09-05'te eklendi: banka gruptan habersizdi ve gaming
    slotlari basarili olup fazlaliklarini bankaya yatirdigi icin banka yapisal
    olarak gaming agirlikliydi. Bankayi harcayan ise hep AC KALAN slot oluyordu,
    yani gaming disi olan — sonuc gunde uc gaming videosuydu."""
    adaylar = [t for t, n in counts_by_topic(kind).items() if n >= need]
    if group == "__hepsi__":
        return adaylar
    return [t for t in adaylar if group_of(kind, t) == group]


def add(kind, item, meta, link, topic, title_topic=None, suffix=None, comments=None,
        group=None):
    """Fazladan indirilen klibi bankaya TASIR (kopyalamaz — kaynak dosya gecici
    klasorde ve calisma sonunda silinecek). Eklendiyse True.

    topic zorunlu: konusu bilinmeyen klip bankaya girmez, cunku sonradan hangi
    videoya ait oldugunu bilemeyiz ve tam da kullanicinin sikayet ettigi
    'alakasiz klipler' sorunu geri gelir."""
    if not topic or room_for(kind) <= 0:
        return False
    video_id = str(meta.get("video_id") or "").strip()
    if not video_id:
        return False
    data = _load()
    entries = data.setdefault(kind, [])
    if any(e.get("video_id") == video_id for e in entries):
        return False

    os.makedirs(BANK_DIR, exist_ok=True)
    filename = f"{video_id}.mp4"
    try:
        shutil.move(item["path"], os.path.join(BANK_DIR, filename))
    except Exception as e:
        print(f"  bankaya tasinamadi ({video_id}): {e}")
        return False

    entries.append({
        "file": filename,
        "topic": topic,
        # Klibi hangi grubun (gaming / gaming disi) urettigi. Bankayi harcarken
        # slotun grubuna gore suzuluyor — bkz. topics_with_enough.
        "group": group,
        "title_topic": title_topic or topic,
        "suffix": suffix or "Fails",
        "video_id": video_id,
        "link": link,
        "title": meta.get("title") or "",
        "comments": list(comments or [])[:5],
        "duration": meta.get("duration"),
        "digg_count": meta.get("digg_count"),
        "mute": bool(item.get("mute")),
        # Tatmin edici klipler bastan kesiliyor (bkz. render.prepare_items);
        # bankadan cikan klip de ayni sekilde davranmali.
        "from_start": bool(item.get("from_start")),
    })
    _save(data)
    return True


def take(kind, topic, need):
    """Bir konudan TAM `need` klip cikarir. Yeterli degilse HICBIR SEY vermez.

    (items, links, metas) doner — gather_items ile ayni sekil. Yarim kadro
    dondurmemesi kasitli: kullanici 3 kliplik banka videosunu acikca reddetti."""
    if need <= 0 or not topic:
        return [], [], []
    available = [e for e in load_bank(kind) if e.get("topic") == topic]
    if len(available) < need:
        return [], [], []
    chosen = sorted(available, key=lambda e: e.get("digg_count") or 0,
                    reverse=True)[:need]

    items, links, metas = [], [], []
    for e in chosen:
        items.append({"path": _entry_path(e), "label": "", "emoji": "",
                      "mute": bool(e.get("mute")),
                      "from_start": bool(e.get("from_start"))})
        links.append(e.get("link") or "")
        metas.append({"video_id": e.get("video_id"), "title": e.get("title") or "",
                      "comments": list(e.get("comments") or []),
                      "duration": e.get("duration"),
                      "digg_count": e.get("digg_count")})

    taken = {e["video_id"] for e in chosen}
    data = _load()
    data[kind] = [e for e in (data.get(kind) or []) if e.get("video_id") not in taken]
    _save(data)
    return items, links, metas


def entry_for_topic(kind, topic):
    """Konunun baslik bilgisi (title_topic/suffix) — banka videosunun basligi
    normal uretimle ayni kuraldan gecsin diye."""
    for e in load_bank(kind):
        if e.get("topic") == topic:
            return {"topic": topic, "title_topic": e.get("title_topic") or topic,
                    "suffix": e.get("suffix") or "Fails"}
    return None


def cleanup_consumed():
    """Manifest'te olmayan mp4'leri siler. Bir banka klibi SADECE BIR KEZ
    kullanilir (kullanici karari); take() kaydi dusuruyor, bu da dosyayi.
    Render BITTIKTEN sonra cagrilmali."""
    if not os.path.isdir(BANK_DIR):
        return 0
    keep = {e["file"] for entries in _load().values() for e in (entries or []) if e.get("file")}
    removed = 0
    for name in os.listdir(BANK_DIR):
        if name in keep:
            continue
        try:
            os.remove(os.path.join(BANK_DIR, name))
            removed += 1
        except OSError:
            pass
    return removed
