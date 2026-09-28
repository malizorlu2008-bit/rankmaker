"""TikTok klip keşfi — tikwm'in kendi arama endpoint'i üzerinden.

Önceki sürüm DuckDuckGo (ddgs) kullanıyordu: sorgu başına ~5-8 link
döndürüyordu ve her aday için ayrıca tikwm'e meta veri sorgusu atmak
gerekiyordu. Arama endpoint'i tek çağrıda 20 sonucu beğeni sayısı, süre,
başlık ve filigransız indirme linkiyle birlikte döndürüyor, sayfalanabiliyor
ve ddgs'in rastgele SSL hataları da ortadan kalkıyor.

Bu geçiş, "en az 100.000 beğeni" şartını uygulanabilir kılan şey: yeterince
büyük bir aday havuzu ancak böyle taranabiliyor.
"""
import time

import requests

class SourceUnavailable(Exception):
    """Kaynagin kendisi erisilemiyor (hepsi 403/timeout) — aday bulunamamasindan
    FARKLI. Bu durumda baska konu denemek anlamsiz: hem CI suresi harciyor hem
    servise daha cok istek atip blogu pekistiriyor (2026-08-13 run #22: kaynak
    403 dondu, kod 5 konuyu bosuna denedi)."""


SEARCH_API = "https://www.tikwm.com/api/feed/search"
PAGE_SIZE = 20
PAGES_PER_QUERY = 5
RATE_LIMIT_SLEEP = 1.1  # tikwm ücretsiz API: saniyede 1 istek


def search_page(keywords, cursor=0):
    res = requests.get(
        SEARCH_API,
        params={"keywords": keywords, "count": PAGE_SIZE, "cursor": cursor},
        timeout=30,
    )
    if not res.ok:
        raise RuntimeError(f"arama servisi yanit vermedi ({res.status_code})")
    data = res.json()
    if data.get("code") != 0:
        raise RuntimeError(data.get("msg") or "arama basarisiz")
    payload = data.get("data") or {}
    return payload.get("videos") or [], payload.get("hasMore"), payload.get("cursor")


def search_videos(queries, pages_per_query=PAGES_PER_QUERY):
    """Sorgu listesini tarayıp benzersiz video kayıtları döndürür.
    Her kayıt: video_id, title, duration, digg_count, play (indirme linki)."""
    if isinstance(queries, str):
        queries = [queries]
    seen = set()
    videos = []
    hatali = 0
    for query in queries:
        cursor = 0
        ilk = True
        for _ in range(pages_per_query):
            try:
                page, has_more, cursor = search_page(query, cursor)
            except Exception as e:
                print(f"  arama hatasi ('{query}'): {e}")
                if ilk:
                    hatali += 1
                break
            ilk = False
            for v in page:
                vid = v.get("video_id")
                if vid and vid not in seen:
                    seen.add(vid)
                    videos.append(v)
            time.sleep(RATE_LIMIT_SLEEP)
            if not has_more:
                break
    if queries and hatali == len(queries):
        raise SourceUnavailable(f"{hatali}/{len(queries)} arama istegi basarisiz")
    return videos


CHALLENGE_INFO_API = "https://www.tikwm.com/api/challenge/info"
CHALLENGE_POSTS_API = "https://www.tikwm.com/api/challenge/posts"
PAGES_PER_TAG = 4


CHALLENGE_ID_RETRIES = 3


def challenge_id(name):
    """Hashtag adini tikwm'in challenge_id'sine cevirir. Bulunamazsa None.

    Neden tekrar deniyor: 2026-08-13'te ayni hashtag bir olcumde cozulup
    digerinde "bulunamadi" dondu (#scareprank, #invisiblechallenge,
    #jumpscareprank). Yani code != 0 cevabi cogu zaman GECICI bir hata, gercekten
    olmayan bir etiket degil — tek denemede vazgecmek o hashtag'in butun
    adaylarini sessizce kaybettiriyordu.
    """
    last_err = None
    for attempt in range(CHALLENGE_ID_RETRIES):
        if attempt:
            time.sleep(RATE_LIMIT_SLEEP * (attempt + 1))
        try:
            res = requests.get(CHALLENGE_INFO_API,
                               params={"challenge_name": name}, timeout=30)
        except Exception as e:
            last_err = e
            continue
        if not res.ok:
            last_err = RuntimeError(f"hashtag servisi yanit vermedi ({res.status_code})")
            continue
        data = res.json()
        if data.get("code") == 0:
            return ((data.get("data") or {}).get("id")) or None
        last_err = None  # servis cevap verdi ama etiketi bulamadi
    if last_err:
        raise last_err
    return None


def hashtag_page(cid, cursor=0):
    res = requests.get(CHALLENGE_POSTS_API,
                       params={"challenge_id": cid, "count": PAGE_SIZE, "cursor": cursor},
                       timeout=30)
    if not res.ok:
        raise RuntimeError(f"hashtag servisi yanit vermedi ({res.status_code})")
    data = res.json()
    if data.get("code") != 0:
        raise RuntimeError(data.get("msg") or "hashtag listesi alinamadi")
    payload = data.get("data") or {}
    return payload.get("videos") or [], payload.get("hasMore"), payload.get("cursor")


def search_by_hashtags(hashtags, pages_per_tag=PAGES_PER_TAG):
    """Hashtag listesinden benzersiz video kayitlari toplar.

    NEDEN VAR: 2026-08-10'da tikwm arama uc noktasi (/api/feed/search)
    Cloudflare bot dogrulamasi arkasina alindi ve 403 donmeye basladi; iki gun
    hic video uretilemedi. Ayni servisin hashtag uc noktalari
    (/api/challenge/info + /api/challenge/posts) ACIK kaldi ve arama ile
    BIREBIR AYNI alanlari donduruyor (video_id, title, duration, digg_count,
    play, author, music_info) — yani asagi akista hicbir sey degismiyor.
    Olculen hacim: hashtag basina 65-75 video, 100k+ begeni suzgecinden 6-66
    tanesi geciyor (5 klip gerekiyor).
    """
    if isinstance(hashtags, str):
        hashtags = [hashtags]
    seen = set()
    videos = []
    hatali = 0
    for tag in hashtags:
        try:
            cid = challenge_id(tag)
        except Exception as e:
            print(f"  hashtag hatasi ('{tag}'): {e}")
            hatali += 1
            continue
        if not cid:
            print(f"  hashtag bulunamadi: #{tag}")
            continue
        time.sleep(RATE_LIMIT_SLEEP)
        cursor = 0
        for _ in range(pages_per_tag):
            # Tek bir sayfa hatasinda o hashtag'i BIRAKMIYORUZ. Eskiden break
            # vardi ve "Free Api Limit: 1 request/second" gibi gecici bir hata
            # hashtag'in kalan sayfalarini tamamen kaybettiriyordu — 2026-08-13'te
            # Kinetic Sand videosunun 5 yerine 3 klipte kalmasinin sebeplerinden
            # biri buydu. Yavaslatip tekrar deniyoruz.
            page = None
            for deneme in range(3):
                try:
                    page, has_more, cursor = hashtag_page(cid, cursor)
                    break
                except Exception as e:
                    print(f"  hashtag sayfa hatasi ('{tag}', deneme {deneme + 1}): {e}")
                    time.sleep(RATE_LIMIT_SLEEP * (deneme + 2))
            if page is None:
                break
            for v in page:
                vid = v.get("video_id")
                if vid and vid not in seen:
                    seen.add(vid)
                    videos.append(v)
            time.sleep(RATE_LIMIT_SLEEP)
            if not has_more:
                break
    if hashtags and hatali == len(hashtags):
        raise SourceUnavailable(f"{hatali}/{len(hashtags)} hashtag istegi basarisiz")
    return videos


# Kaynak kisitlamasi gecici oldugunda gunu bosa harcamamak icin, calismanin
# KENDI ICINDE bekleyip tekrar deniyoruz. 2026-08-13: 09:00 ve 14:48
# calismalari kaynaga sorunsuz eristi, ama 1 saat icinde dort calisma
# tetikleyince tikwm kisitladi ve 15:19'dan sonrakiler 403 aldi. Yani blok
# kalici degil, kendi istek yogunlugumuzun sonucu. 4 dakikalik araliklarla 3
# deneme, 90 dakikalik is siniri icinde rahat siginiyor.
# 3 deneme x 240sn iken 2'ye x 60sn'ye indirildi. Sebep (2026-08-14, run #26
# log'u): kaynak CI'dan 403 dondugunde UC deneme de 403 aldi ve iki video icin
# ~16 dakika CI suresi bosa gitti. Ayni is icinde tekrar denemek IP'yi
# DEGISTIRMIYOR — blok IP bazli oldugu icin ayni sunucudan tekrar denemenin
# faydasi yok. Yeni IP ancak YENI BIR CALISMA ile geliyor; o yuzden cozum ayni
# gun ikinci kez tetiklemek (calisma artik dolu saatleri atliyor, guvenli).
# Kisa tek bir tekrar yine de duruyor: gercekten anlik bir kesinti olabilir.
SOURCE_RETRY_ATTEMPTS = 2
SOURCE_RETRY_WAIT = 60


def _topla(hashtags, queries):
    """Tek bir deneme: hashtag ucu, olmazsa arama ucu."""
    hashtag_kapali = False
    videos = []
    if hashtags:
        try:
            videos = search_by_hashtags(hashtags)
        except SourceUnavailable as e:
            print(f"  hashtag ucu erisilemiyor: {e}")
            hashtag_kapali = True
    if videos:
        return videos
    if queries:
        print("  hashtag'lerden sonuc yok, arama uc noktasi deneniyor")
        try:
            return search_videos(queries)
        except SourceUnavailable as e:
            if hashtag_kapali:
                raise
            print(f"  arama ucu erisilemiyor: {e}")
            return []
    if hashtag_kapali:
        raise SourceUnavailable("kaynak erisilemiyor")
    return []


def find_videos(hashtags=None, queries=None):
    """Once hashtag ucundan toplar, olmazsa arama ucunu dener. Kaynak tamamen
    erisilemezse bekleyip yeniden dener (bkz. SOURCE_RETRY_ATTEMPTS)."""
    for deneme in range(SOURCE_RETRY_ATTEMPTS):
        try:
            return _topla(hashtags, queries)
        except SourceUnavailable as e:
            if deneme == SOURCE_RETRY_ATTEMPTS - 1:
                raise
            print(f"  kaynak erisilemiyor ({e}); {SOURCE_RETRY_WAIT}sn bekleyip "
                  f"tekrar denenecek ({deneme + 2}/{SOURCE_RETRY_ATTEMPTS})")
            time.sleep(SOURCE_RETRY_WAIT)
    return []


def video_link(video):
    """Kanonik TikTok linki — tekrar önleme state'inde kimlik olarak kullanılır."""
    author = (video.get("author") or {}).get("unique_id") or "user"
    return f"https://www.tiktok.com/@{author}/video/{video.get('video_id')}"


COMMENT_API = "https://www.tikwm.com/api/comment/list"


def top_comments(video_id, count=5):
    """En beğenilen yorumlar — etiket üretirken klibin ne olduğunu anlamaya
    yardımcı oluyor (açıklama çoğu zaman sadece hashtag). Hata durumunda boş
    liste döner, akış durmaz."""
    try:
        res = requests.get(COMMENT_API, params={"url": video_id, "count": count}, timeout=30)
        data = res.json()
        if data.get("code") != 0:
            return []
        comments = (data.get("data") or {}).get("comments") or []
    except Exception:
        return []
    texts = []
    for c in comments:
        text = " ".join((c.get("text") or "").split())
        if text:
            texts.append(text[:120])
    return texts


def is_original_sound(video):
    """TikTok'un kendi 'music_info.original' alani: True ise klibi cekenin
    kendi kaydettigi ses (ortam sesi, konusma, gulme) demektir - guvenli.
    False ise TikTok'ta hazir/bilinen bir ses/sarki kullanilmis demektir;
    bu genelde lisansli muzik oluyor ve YouTube Content ID'ye takilabiliyor
    (2026-08-04: "Oh No" - Kreapa ve "Dancing In The Dark" - Rihanna boyle
    iki videoyu dunya genelinde engelletti). Alan eksikse iyimser davranip
    True donuyoruz - kullanici klip seslerinin varsayilan olarak kalmasini
    istedi, sadece bilinen riskli sesler susturulsun."""
    music = video.get("music_info") or video.get("music") or {}
    original = music.get("original")
    return True if original is None else bool(original)


def download_info(video):
    """Arama sonucunu tiktok.download_from_info'nun beklediği şekle çevirir."""
    return {
        "video_url": video.get("play") or video.get("hdplay"),
        "title": video.get("title") or "",
        "duration": video.get("duration"),
    }
