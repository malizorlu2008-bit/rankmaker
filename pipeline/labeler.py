"""Sıralama etiketlerini Google Gemini ile üretir.

Neden AI: klibin TikTok açıklaması çoğu zaman ya hashtag yığını ya da
anlamsız oluyor ("Yeah I coming soon"), hazır bir etiket havuzundan rastgele
seçmek de içerikle eşleşmiyordu. Açıklama + en beğenilen yorumlar birlikte
verildiğinde bir dil modeli klibi tarif eden kısa bir etiket üretebiliyor.

Neden Gemini: kullanıcının şartı sıfır maliyet (2026-08-02 kararı). Ücretsiz
katman günde 1.500 istek veriyor, kredi kartı istemiyor; bizim ihtiyacımız
günde 2 istek.

Anahtar yoksa veya çağrı başarısız olursa etiketler boş kalır ve video yine
üretilir — bu modül asla günlük akışı durdurmaz.
"""
import json
import os
import time

import requests

API_BASE = "https://generativelanguage.googleapis.com/v1beta"
API_URL = API_BASE + "/models/{model}:generateContent"
LIST_URL = API_BASE + "/models"
# Google model adlarini emekliye ayiriyor (2026-08-02'de gemini-2.5-flash
# "no longer available to new users" dedi). Sirayla denenir; hepsi olmazsa
# ListModels ile o an gecerli bir flash modeli bulunur.
# gemini-2.0-flash 2026-08-22'de listeden CIKARILDI: generateContent'e HTTP 404
# donduruyor, yani iyi modeller zaman asimina ugradiginda arkada duran yedek de
# olu cikiyordu. Bir model 404 vermeye baslarsa buradan cikarilmali; ListModels
# tabanli _discover_model zaten son care olarak duruyor.
CANDIDATE_MODELS = ["gemini-3.6-flash", "gemini-flash-latest", "gemini-2.5-flash-lite"]
MAX_LABEL_CHARS = 17  # kullanici istegi 2026-08-02: daha kisa, esprili etiketler
MAX_HASHTAGS = 10     # YouTube 15+ hashtag gorurse hepsini yok sayiyor
# 60 -> 30. Zaman asimi artik TEKRAR DENENIYOR (bkz. _try_model), yani tek bir
# cagrinin uzun beklemesi gereksiz: dort deneme x 30sn, 60sn'lik TEK denemeden
# hem daha dayanikli hem daha hizli. Etiket uretimi flash modelde tipik olarak
# saniyeler suruyor; 30sn zaten bol pay. Ust sinir onemli cunku tam kesintide
# 4 video x 3 model x 4 deneme CI'nin 170 dakikasini yiyebilir ve is zaman
# asimina ugrarsa state commit'i de kaybolur.
TIMEOUT = 30
TRANSIENT_STATUSES = (429, 500, 503)
RETRY_DELAYS = (5, 15, 30)

PROMPT = """You are preparing a YouTube Shorts video titled "{video_title}".

Below are {n} TikTok clips that appear in the video. For each one you get its caption and its most-liked comments.

Produce two things.

1) "labels": ONE punchy on-screen label per clip.
- Write it as a REACTION, the way a viewer would blurt it out watching the clip —
  NOT as a description of what happens. This is the single most important rule.
- Good (reaction voice): "Hell naw", "WTF", "Not again smh", "Why tho", "I'm sorry",
  "Called it", "Nope nope nope", "That's gotta hurt"
- Bad (description voice): "Sand bellyflop", "The classic wipeout", "Tent goes swimming",
  "Man falls into the water" — never write labels like these
- English, VERY short: 1-3 words, hard max {max_chars} characters
- Casual/texting register is good (smh, bruh, ngl, lowkey). Slightly misspelled for
  comic effect is fine.
- It should still make sense for that specific clip — a viewer who sees the clip
  should feel the label nails their reaction
- No hashtags, no emoji, no quotes, no ending punctuation, do not number them
- Each label must be different
- Return exactly {n} labels, in the same order as the clips

2) "question": ONE short comment the channel will post under its own video to start an argument in the replies.
- It must be a QUESTION about the ranking itself, so viewers reply to defend their pick
- Tie it to what is actually in these clips, e.g. "Which raccoon deserved #1?",
  "Be honest, was #1 the right pick?", "#3 or #1, pick your fighter"
- Never beg for subscribers, likes or follows
- English, casual, one line, max 100 characters, at most one emoji, no hashtags

3) "hashtags": 10 YouTube hashtags that will actually help THIS video get found.
- Base them on what is really in these specific clips, not generic filler
- Mix: 2-3 broad high-traffic terms, and 6-7 specific/niche ones tied to the actual content
- Lowercase, no "#", no spaces (join words: campingfail), no punctuation
- No duplicates, no near-duplicates
- Always include "shorts"

Clips:
{clips}"""


def _clip_block(index, meta):
    lines = [f"--- Clip {index + 1} ---", f"Caption: {meta.get('title') or '(none)'}"]
    comments = meta.get("comments") or []
    if comments:
        lines.append("Top comments: " + " | ".join(comments))
    return "\n".join(lines)


def _sanitize(label):
    text = str(label or "").strip().strip('"“”').strip()
    text = " ".join(text.split())
    if len(text) > MAX_LABEL_CHARS:
        text = text[:MAX_LABEL_CHARS].rsplit(" ", 1)[0]
    return text


def _try_model(model, body, api_key):
    """(metin, baska_model_denensin_mi). Gecici hatalarda (yogunluk/kota)
    ayni model birkac kez tekrar denenir — 2026-08-02'de ikinci cagri
    HTTP 503 "high demand" alip etiketsiz kalmisti."""
    for attempt in range(len(RETRY_DELAYS) + 1):
        try:
            res = requests.post(
                API_URL.format(model=model),
                headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                json=body,
                timeout=TIMEOUT,
            )
        except Exception as e:
            # AG HATASI DA GECICI. Eskiden burada aninda pes ediliyordu ve
            # RETRY_DELAYS sadece HTTP 429/500/503'e isliyordu — oysa Gemini'nin
            # en sik verdigi ariza okuma zaman asimi, yani bir ISTISNA.
            # 2026-08-22 10:00 calismasi tam bunu yasadi: 'gemini-3.6-flash' bir
            # 503'ten sonra timeout'a dustu ve tek denemede birakildi,
            # 'gemini-flash-latest' ilk timeout'ta birakildi, gunun iki
            # gaming-disi videosu (18:00 ve 20:00) klipleri indirilmis halde
            # etiketsiz kaldigi icin CoPE atildi.
            if attempt < len(RETRY_DELAYS):
                delay = RETRY_DELAYS[attempt]
                print(f"  '{model}' cagrilamadi ({e}); {delay}sn sonra tekrar")
                time.sleep(delay)
                continue
            print(f"  '{model}' cagrilamadi, denemeler tukendi: {e}")
            return None, True

        if res.status_code in (400, 404):
            print(f"  '{model}' kullanilamiyor (HTTP {res.status_code}), siradaki model deneniyor")
            return None, True

        if res.status_code in TRANSIENT_STATUSES:
            if attempt < len(RETRY_DELAYS):
                delay = RETRY_DELAYS[attempt]
                print(f"  '{model}' gecici hata (HTTP {res.status_code}), {delay}sn sonra tekrar")
                time.sleep(delay)
                continue
            print(f"  '{model}' gecici hatalari asilamadi (HTTP {res.status_code})")
            return None, True  # baska bir model daha az yogun olabilir

        if not res.ok:
            print(f"  etiket uretilemedi (HTTP {res.status_code}): {res.text[:200]}")
            return None, False

        try:
            return res.json()["candidates"][0]["content"]["parts"][0]["text"], False
        except Exception as e:
            print(f"  yanit okunamadi: {e}")
            return None, False
    return None, True


def _discover_models(api_key):
    """Model adlari zamanla emekliye ayriliyor — o an gecerli flash modellerin
    LISTESINI dondurur.

    Eskiden tek bir ad donuyordu ve o ad 404 verirse is bitiyordu. 2026-08-22'de
    tam bu oldu: ListModels 'gemini-2.5-flash' onerdi, generateContent ona 404
    dedi ve son care de tukendi. Liste donunce hepsi sirayla denenebiliyor —
    ListModels bir modeli listeliyor olmasi o anahtarin onu CAGIRABILECEGI
    anlamina gelmiyor.
    """
    try:
        res = requests.get(LIST_URL, headers={"x-goog-api-key": api_key}, timeout=TIMEOUT)
        if not res.ok:
            return []
        models = res.json().get("models") or []
    except Exception:
        return []
    usable = [
        m["name"].split("/")[-1]
        for m in models
        if "generateContent" in (m.get("supportedGenerationMethods") or [])
        and "flash" in m.get("name", "")
        and "thinking" not in m.get("name", "")
    ]
    return usable


def _call_with_fallback(body, api_key, model=None):
    tried = []
    for candidate in [model or os.environ.get("GEMINI_MODEL")] + CANDIDATE_MODELS:
        if not candidate or candidate in tried:
            continue
        tried.append(candidate)
        text, keep_trying = _try_model(candidate, body, api_key)
        if text is not None:
            return text
        if not keep_trying:
            return None

    for discovered in _discover_models(api_key):
        if discovered in tried:
            continue
        tried.append(discovered)
        print(f"  otomatik model deneniyor: {discovered}")
        text, keep_trying = _try_model(discovered, body, api_key)
        if text is not None:
            return text
        if not keep_trying:
            return None
    print(f"  etiket uretilemedi: denenen modeller {', '.join(tried)}")
    return None


def _sanitize_hashtag(tag):
    clean = "".join(ch for ch in str(tag or "").lower() if ch.isalnum())
    return clean if len(clean) >= 3 else ""


def generate_metadata(clips_meta, video_title, api_key=None, model=None):
    """clips_meta: [{"title": caption, "comments": [str, ...]}, ...]

    {"labels": [...], "hashtags": [...]} döner. labels her zaman clips_meta
    ile aynı uzunlukta (hata olursa boş string'lerle), hashtags hata olursa
    boş liste — çağıran taraf kendi yedeğine düşebilsin."""
    empty = {"labels": [""] * len(clips_meta), "question": "", "hashtags": []}
    if not clips_meta:
        return empty

    api_key = api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("  etiket atlandi: GEMINI_API_KEY tanimli degil")
        return empty

    prompt = PROMPT.format(
        video_title=video_title,
        n=len(clips_meta),
        max_chars=MAX_LABEL_CHARS,
        clips="\n\n".join(_clip_block(i, m) for i, m in enumerate(clips_meta)),
    )
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.7,
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "OBJECT",
                "properties": {
                    "labels": {"type": "ARRAY", "items": {"type": "STRING"}},
                    "question": {"type": "STRING"},
                    "hashtags": {"type": "ARRAY", "items": {"type": "STRING"}},
                },
                "required": ["labels", "question", "hashtags"],
            },
        },
    }

    text = _call_with_fallback(body, api_key, model)
    if text is None:
        return empty
    try:
        data = json.loads(text)
        labels = [_sanitize(x) for x in (data.get("labels") or [])]
        hashtags = []
        for tag in data.get("hashtags") or []:
            clean = _sanitize_hashtag(tag)
            if clean and clean not in hashtags:
                hashtags.append(clean)
    except Exception as e:
        print(f"  etiket cozumlenemedi: {e}")
        return empty

    if len(labels) < len(clips_meta):
        labels += [""] * (len(clips_meta) - len(labels))
    return {
        "labels": labels[:len(clips_meta)],
        # Yorum metni burada SADECE tasiniyor; temizligi pipeline/comment.py
        # yapiyor (hashtag ayikla, tek satira indir, kirp) — yorum oradan
        # gonderiliyor ve kural tek yerde dursun.
        "question": " ".join(str(data.get("question") or "").split()),
        "hashtags": hashtags[:MAX_HASHTAGS],
    }
