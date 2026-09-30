"""Konu havuzunu OLCER: hangi konu su anda kac uygun klip veriyor.

Iki isi var:

1. ADAY KONU ELEME — yeni konu fikirlerini eklemeden once olcer. Konu eklemenin
   kurali (2026-08-13'te yasandi): olculmemis konu eklemek videonun atlanmasina
   yol aciyor, cunku bazi hashtag'ler 0-1 aday veriyor.

2. TREND DESTEGI — mevcut konularin GUNCEL verimini olcup
   pipeline/state/topic_scores.json'a yazar. daily_run konu secerken bu skoru
   kullanip son taramada bol klip veren konulari one aliyor. "Trend" burada
   haber degil, kaynagin o an ne kadar taze/populer kisa klip verdigi.

Kullanim:
    python3 -m pipeline.topic_scan                # mevcut konulari olc, kaydet
    python3 -m pipeline.topic_scan --adaylar      # aday konu listesini olc
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from pipeline import daily_run as dr, discovery  # noqa: E402

SCORES_PATH = os.path.join(HERE, "state", "topic_scores.json")
# Uretimdekiyle AYNI sayfa sayisi. Once 2 denendi ("tikwm'i yormayalim") ve
# yanlis karar verdirdi: 2 sayfayla zayif gorunen dort konu 4 sayfada esigi
# rahat gecti (Skate 4->14, Slip And Slide 3->9, Scare Cam 3->7, Trampoline
# 4->6). Olcum uretimden farkli kosulda yapilirsa olcum degil tahmin olur.
# Istekler ISTEKLER_ARASI kadar aralikli gonderiliyor, yuk sorunu bu sekilde
# cozuluyor — sayfa sayisini dusurerek degil.
PAGES = 4
ISTEKLER_ARASI = 1.2

# Aday konular: eklenmeden ONCE olculur, esigi gecen eklenir.
ADAY_KONULAR = [
    # 2026-09-05 dalgasi. Sebep: gunde dort videoya cikinca havuz kurudu —
    # 451 klip harcandi, skorlar iki haftada yariya indi ve sekiz gaming-disi
    # konu bes klip esiginin altina dustu. Cozum bar dusurmek degil (kullanici
    # reddetti) konu sayisini artirmak.

    # --- GAMING ---
    # Valorant ve CS2 kullanicinin acik istegi. 2026-08-20'de SAYIYLA gectiler
    # (24 ve 32) ama ICERIKLE elendiler: valorantclips/valorantace gibi
    # esports etiketleri "buy a sheriff on an eco" tarzi oyun bilgisi isteyen
    # klipler getiriyordu. Bu kez KOMIK-AN etiketleriyle olculuyor.
    # DIKKAT: required icine turnuva kelimesi (major, blast, iem) konmuyor —
    # yayin goruntusu #fifa/#messi gibi goruntu bazli Content ID riski demek.
    {"topic": "Valorant Moments", "title_topic": "Valorant", "suffix": "Moments",
     "group": "gaming", "required": ("valorant", "jett", "sage", "spike"),
     "hashtags": ["valorantfunny", "valorantfails", "valorantmoments", "valoranttiktok"]},
    {"topic": "CS2 Moments", "title_topic": "CS2", "suffix": "Moments",
     "group": "gaming", "required": ("cs2", "csgo", "counter strike"),
     "hashtags": ["cs2funny", "cs2fails", "csgofunny", "cs2memes"]},
    {"topic": "Lethal Company", "title_topic": "Lethal Company", "suffix": "Moments",
     "group": "gaming", "required": ("lethal company", "lethal"),
     "hashtags": ["lethalcompany", "lethalcompanyclips", "lethalcompanyfunny"]},
    {"topic": "Phasmophobia", "title_topic": "Phasmophobia", "suffix": "Moments",
     "group": "gaming", "required": ("phasmophobia", "ghost"),
     "hashtags": ["phasmophobia", "phasmophobiafunny", "phasmophobiaclips"]},
    {"topic": "Fall Guys", "title_topic": "Fall Guys", "suffix": "Moments",
     "group": "gaming", "required": ("fall guys", "fallguys"),
     "hashtags": ["fallguys", "fallguysclips", "fallguysfunny"]},
    {"topic": "Stumble Guys", "title_topic": "Stumble Guys", "suffix": "Moments",
     "group": "gaming", "required": ("stumble guys", "stumbleguys"),
     "hashtags": ["stumbleguys", "stumbleguysfunny", "stumbleguysclips"]},
    {"topic": "Among Us", "title_topic": "Among Us", "suffix": "Moments",
     "group": "gaming", "required": ("among us", "amongus", "imposter", "sus"),
     "hashtags": ["amongus", "amongusfunny", "amongusmemes"]},
    {"topic": "Mario Kart", "title_topic": "Mario Kart", "suffix": "Moments",
     "group": "gaming", "required": ("mario kart", "mariokart", "blue shell"),
     "hashtags": ["mariokart", "mariokartfunny", "mariokart8"]},
    {"topic": "Gorilla Tag", "title_topic": "VR", "suffix": "Moments",
     "group": "gaming", "required": ("gorilla tag", "vr", "quest"),
     "hashtags": ["gorillatag", "vrfails", "vrfunny"]},
    {"topic": "Elden Ring Deaths", "title_topic": "Elden Ring", "suffix": "Moments",
     "group": "gaming", "required": ("elden ring", "eldenring", "boss", "death"),
     "hashtags": ["eldenring", "eldenringclips", "eldenringfunny"]},

    # --- GAMING DISI ---
    # Hepsi hayvan ya da fiziksel komedi: oynamayanin/bilmeyenin de anladigi
    # gorsel espri. Kanalin en iyi konulari (Horse, Football, Wedding) da ayni
    # ailedendi.
    {"topic": "Capybara", "title_topic": "Capybara", "suffix": "Moments",
     "required": ("capybara", "capy"), "hashtags": ["capybara", "capybaratiktok", "funnycapybara"]},
    {"topic": "Otter Moments", "title_topic": "Otter", "suffix": "Moments",
     "required": ("otter",), "hashtags": ["otter", "ottersoftiktok", "funnyotter"]},
    {"topic": "Sheep Moments", "title_topic": "Sheep", "suffix": "Moments",
     "required": ("sheep", "lamb", "ram"), "hashtags": ["sheep", "sheepoftiktok", "funnysheep"]},
    {"topic": "Cow Moments", "title_topic": "Cow", "suffix": "Moments",
     "required": ("cow", "calf", "cattle"), "hashtags": ["cowsoftiktok", "funnycow", "cowtok"]},
    {"topic": "Goose Attack", "title_topic": "Goose", "suffix": "Moments",
     "required": ("goose", "geese"), "hashtags": ["goose", "gooseattack", "funnygoose"]},
    {"topic": "Seagull Stealing", "title_topic": "Seagull", "suffix": "Moments",
     "required": ("seagull", "gull", "stealing"), "hashtags": ["seagull", "seagullstealing", "birdstealingfood"]},
    {"topic": "Squirrel Moments", "title_topic": "Squirrel", "suffix": "Moments",
     "required": ("squirrel", "chipmunk"), "hashtags": ["squirrel", "squirreltiktok", "funnysquirrel"]},
    {"topic": "Elephant Moments", "title_topic": "Elephant", "suffix": "Moments",
     "required": ("elephant",), "hashtags": ["elephant", "elephantsoftiktok", "funnyelephant"]},
    {"topic": "Zoo Animals", "title_topic": "Zoo", "suffix": "Moments",
     "required": ("zoo", "enclosure", "keeper"), "hashtags": ["zoo", "zooanimals", "zootiktok"]},
    {"topic": "Cat Vs Cucumber", "title_topic": "Scared Cat", "suffix": "Moments",
     "required": ("cat", "cucumber", "scared"), "hashtags": ["catvscucumber", "scaredcat", "funnycatsoftiktok"]},
    {"topic": "Puppy Fail", "title_topic": "Puppy", "suffix": "Moments",
     "required": ("puppy", "pup"), "hashtags": ["puppyfail", "funnypuppy", "puppytiktok"]},
    {"topic": "Toddler Vs Dog", "title_topic": "Toddler And Dog", "suffix": "Moments",
     "required": ("toddler", "baby", "dog"), "hashtags": ["toddleranddog", "babyanddog", "kidsanddogs"]},
    {"topic": "Dog Bath", "title_topic": "Dog Bath", "suffix": "Moments",
     "required": ("bath", "dog", "shower"), "hashtags": ["dogbath", "dogbathtime", "funnydogbath"]},
    {"topic": "Karaoke Fail", "title_topic": "Karaoke", "suffix": "Fails",
     "required": ("karaoke", "singing", "sing"), "hashtags": ["karaokefail", "singingfail", "funnykaraoke"]},
    {"topic": "Proposal Fail", "title_topic": "Proposal", "suffix": "Fails",
     "required": ("proposal", "propose", "engaged"), "hashtags": ["proposalfail", "proposalgonewrong", "failedproposal"]},
    {"topic": "Cake Fail", "title_topic": "Cake", "suffix": "Fails",
     "required": ("cake", "baking", "frosting"), "hashtags": ["cakefail", "bakingfail", "cakefails"]},
    {"topic": "Golf Fail", "title_topic": "Golf", "suffix": "Fails",
     "required": ("golf", "swing", "tee"), "hashtags": ["golffail", "golffails", "funnygolf"]},
    {"topic": "Bowling Fail", "title_topic": "Bowling", "suffix": "Fails",
     "required": ("bowling", "bowl", "strike"), "hashtags": ["bowlingfail", "bowlingfails", "funnybowling"]},
    {"topic": "Treadmill Fail", "title_topic": "Treadmill", "suffix": "Fails",
     "required": ("treadmill", "gym", "running"), "hashtags": ["treadmillfail", "gymfail", "treadmillfails"]},
    {"topic": "Shopping Cart", "title_topic": "Shopping Cart", "suffix": "Fails",
     "required": ("cart", "trolley", "shopping"), "hashtags": ["shoppingcartfail", "trolleyfail", "cartfail"]},
    {"topic": "Snowboard Fail", "title_topic": "Snowboard", "suffix": "Fails",
     "required": ("snowboard", "board", "slope"), "hashtags": ["snowboardfail", "snowboardfails", "funnysnowboard"]},
    {"topic": "Surfing Fail", "title_topic": "Surfing", "suffix": "Fails",
     "required": ("surf", "wave", "wipeout"), "hashtags": ["surffail", "surfingfail", "wipeout"]},
    {"topic": "Jet Ski Fail", "title_topic": "Jet Ski", "suffix": "Fails",
     "required": ("jet ski", "jetski", "wave runner"), "hashtags": ["jetskifail", "jetski", "jetskifails"]},
    {"topic": "Pool Fail", "title_topic": "Pool", "suffix": "Fails",
     "required": ("pool", "dive", "swim"), "hashtags": ["poolfail", "poolfails", "divingfail"]},
    {"topic": "BBQ Fail", "title_topic": "BBQ", "suffix": "Fails",
     "required": ("bbq", "grill", "barbecue"), "hashtags": ["bbqfail", "grillfail", "cookingfail"]},
    {"topic": "Delivery Fail", "title_topic": "Delivery", "suffix": "Fails",
     "required": ("delivery", "package", "driver", "courier"), "hashtags": ["deliveryfail", "packagefail", "deliverydriver"]},

    # Bu besi 2026-08-13'te SADECE IKI SAYFAYLA olculup elenmisti (hepsi 1
    # aday). Tarama o gunden beri dort sayfa; ayni degisiklik Skate'i 4->14,
    # Slip And Slide'i 3->9, Scare Cam'i 3->7 yapmisti. Yeniden olculuyorlar.
    {"topic": "Truck Fail", "title_topic": "Truck", "suffix": "Fails",
     "required": ("truck", "trailer", "lorry"), "hashtags": ["truckfail", "truckfails", "truckerlife"]},
    {"topic": "Ladder Fail", "title_topic": "Ladder", "suffix": "Fails",
     "required": ("ladder", "roof", "climb"), "hashtags": ["ladderfail", "diyfail", "constructionfail"]},
    {"topic": "Bike Fail", "title_topic": "Bike", "suffix": "Fails",
     "required": ("bike", "bicycle", "cycling"), "hashtags": ["bikefail", "bicyclefail", "bmxfail"]},
    {"topic": "Monkey Stealing", "title_topic": "Monkey", "suffix": "Moments",
     "required": ("monkey", "macaque", "ape"), "hashtags": ["monkeystealing", "funnymonkey", "monkeytiktok"]},
    {"topic": "Forklift Fail", "title_topic": "Forklift", "suffix": "Fails",
     "required": ("forklift", "warehouse", "pallet"), "hashtags": ["forkliftfail", "warehousefail", "forkliftfails"]},

    # --- FUTBOL DALGASI (2026-09-30, kullanici istegi: "football messi ronaldo") ---
    #
    # ONEMLI TELIF NOTU: Messi/Ronaldo etiketleri MAC YAYIN GORUNTUSU cekiyor.
    # Bu kanalin en yuksek Content ID riski olan icerik turu ve bu dosyanin
    # basinda zaten yazili ("yayin goruntusu #fifa/#messi gibi goruntu bazli
    # Content ID riski demek"). Kanalda hali hazirda iki bolge engeli var.
    # Kullanici karari: ikisi de olculsun, VERI VE ICERIK karar versin — yani
    # ornek kliplere bakilacak ve yayin goruntusu agir basiyorsa o konu
    # EKLENMEYECEK. Yaninda telif riski dusuk (kullanici uretimi) futbol
    # konulari da olculuyor ki, unlu isim konulari elenirse futbol kategorisi
    # tamamen bos kalmasin.
    #
    # Ek baglam: mevcut "Football Fail" konusunun izlenme medyani 1.290 ve
    # 2026-09-30'da rotasyondan dusen dort konudan biri. Yani bu kanalda
    # futbol-fail tutmadi; asagidakiler FARKLI bir icerik turu (beceri/an).
    #
    # required icine turnuva/lig kelimesi (champions, world cup, la liga)
    # BILEREK konmuyor — o kelimeler dogrudan yayin goruntusu getiriyor.

    # Istenen isim konulari (yuksek risk, icerik kontrolu sart):
    {"topic": "Messi Moments", "title_topic": "Messi", "suffix": "Moments",
     "required": ("messi", "inter miami", "argentina"),
     "hashtags": ["messi", "messiskills", "messimagic", "messifans"]},
    {"topic": "Ronaldo Moments", "title_topic": "Ronaldo", "suffix": "Moments",
     "required": ("ronaldo", "cr7", "siu", "al nassr"),
     "hashtags": ["ronaldo", "cr7", "ronaldoskills", "siuuu"]},

    # Telif riski dusuk, kullanici uretimi futbol:
    # suffix "Skills": "Ranking Craziest Football Skills" -> "... Football
    # Skills Moments"tan cok daha temiz. adjectives_for OLUMLU_SONEKLER'e
    # eklendi, yoksa "Worst Football Skills" gibi bir baslik cikardi.
    {"topic": "Football Skills", "title_topic": "Football", "suffix": "Skills",
     "required": ("skill", "dribble", "football", "soccer"),
     "hashtags": ["footballskills", "dribbling", "footballtiktok", "soccerskills"]},
    {"topic": "Freestyle Football", "title_topic": "Freestyle Football", "suffix": "Moments",
     "required": ("freestyle", "juggling", "panna", "street"),
     "hashtags": ["freestylefootball", "panna", "streetfootball", "footballfreestyle"]},
    {"topic": "Football Fan Moments", "title_topic": "Football Fan", "suffix": "Moments",
     "required": ("fan", "crowd", "stadium", "celebration"),
     "hashtags": ["footballfans", "stadiumvibes", "footballcelebration", "footballculture"]},
]


def konu_olc(cfg, used_links, min_digg=None, max_seconds=None):
    """(taranan, uygun_aday, hata_mi) — filtreler uretimdekiyle ayni.

    Ucuncu deger NEDEN var: eskiden istek patladiginda da 0 doniyordu ve
    "olctum, hicbir sey yok" ile "olcemedim" ayirt edilemiyordu. 2026-08-20
    bulut taramasinin 25 konusu da 0 yazildi — oysa ayni calisma iki video
    uretti, yani kaynak ayaktaydi; sadece hashtag ucu o runner'a kapaliydi
    (uretim discovery.find_videos ile arama ucuna dusup kurtuluyor, tarama
    dogrudan search_by_hashtags cagirdigi icin duseceigi yer yok). Sifirlar
    BUGUNUN tarihiyle kaydedilince bayat_mi() uc gun False donuyor ve konu
    secimi o sure boyunca skorsuz kaliyor.
    """
    min_digg = dr.MIN_DIGG_COUNT if min_digg is None else min_digg
    max_seconds = max_seconds or dr.max_candidate_seconds(dr.CLIP_COUNT)
    try:
        vids = discovery.search_by_hashtags(cfg["hashtags"], pages_per_tag=PAGES)
    except Exception as e:
        print(f"  {cfg['topic']}: HATA {str(e)[:50]}")
        return 0, 0, True
    gerekli = cfg.get("required") or ()
    uygun = [
        v for v in vids
        if discovery.video_link(v) not in used_links
        and (v.get("digg_count") or 0) >= min_digg
        and dr.MIN_CLIP_SECONDS <= (v.get("duration") or 0) <= max_seconds
        and (not gerekli or any(w in (v.get("title") or "").lower() for w in gerekli))
        and not dr.is_reaction_title(v.get("title") or "")
        and not dr.uygunsuz_baslik(v.get("title") or "")
    ]
    return len(vids), len(uygun), False


# Taramanin gecerli sayilmasi icin en az bu kadar konu klip vermeli. Butun
# konularin 0 cikmasi bir OLCUM degil, basarisiz bir tarama: konu havuzunun
# tamami ayni gun kurumaz. Boyle bir sonucu kaydetmek eski (dogru) skorlari
# siler ve tarihi tazeledigi icin uc gun boyunca yeniden denenmesini de
# engeller — 2026-08-20'de tam olarak bu oldu.
GECERLI_TARAMA_ESIGI = 1


def tara(konular, kaydet=False):
    used = dr.load_used_clips()
    sonuc = {}
    hatali = 0
    print(f"{'konu':<24}{'taranan':>9}{'uygun':>8}")
    for cfg in konular:
        taranan, uygun, hata = konu_olc(cfg, used)
        if hata:
            hatali += 1
        sonuc[cfg["topic"]] = uygun
        isaret = "  ✓" if uygun >= dr.CLIP_COUNT else ("  zayif" if uygun else "  BOS")
        print(f"{cfg['topic'][:22]:<24}{taranan:>9}{uygun:>8}{isaret}")
        time.sleep(ISTEKLER_ARASI)
    if kaydet:
        veren = sum(1 for v in sonuc.values() if v)
        if veren < GECERLI_TARAMA_ESIGI:
            print(f"\nTARAMA BASARISIZ: {len(sonuc)} konunun {hatali} tanesi hata "
                  f"verdi, hicbiri klip bulamadi — skor dosyasina DOKUNULMUYOR, "
                  f"eski skorlar korunuyor ve yarin tekrar denenecek.",
                  file=sys.stderr)
            return sonuc
        os.makedirs(os.path.dirname(SCORES_PATH), exist_ok=True)
        with open(SCORES_PATH, "w") as f:
            json.dump({"tarih": dr.bugun(), "skorlar": sonuc}, f,
                      ensure_ascii=False, indent=2)
        print(f"\nskorlar yazildi: {SCORES_PATH}")
    return sonuc


def skorlar():
    """{konu: uygun_aday}. Dosya yoksa bos — cagiran taraf skorsuz calisir."""
    try:
        with open(SCORES_PATH) as f:
            return (json.load(f) or {}).get("skorlar") or {}
    except Exception:
        return {}


# Skorlar kac gunde bir tazelensin. Her calismada taramak 300 istek + ~10 dakika
# demek ve konu verimi gunluk degismiyor; 3 gun makul bir orta yol.
TAZELEME_GUNU = 3


def bayat_mi():
    """Skor dosyasi yok ya da TAZELEME_GUNU'nden eskiyse True."""
    try:
        with open(SCORES_PATH) as f:
            tarih = (json.load(f) or {}).get("tarih")
    except Exception:
        return True
    if not tarih:
        return True
    from datetime import date
    try:
        y, a, g = (int(x) for x in tarih.split("-"))
    except ValueError:
        return True
    return (date.today() - date(y, a, g)).days >= TAZELEME_GUNU


def gerekiyorsa_tara(konular):
    """Skorlar bayatsa yeniden olcer. Hata durumunda sessizce eski skorlarla
    devam eder — tarama gunluk akisi ASLA durdurmamali."""
    if not bayat_mi():
        return False
    print(f"konu skorlari {TAZELEME_GUNU} gunden eski — yeniden olculuyor")
    try:
        tara(konular, kaydet=True)
        return True
    except Exception as e:
        print(f"konu taramasi yapilamadi ({e}) — eski skorlarla devam", file=sys.stderr)
        return False


def ornekleri_bas(cfg, adet=7):
    """Bir adayin en cok begenilen kliplerinin ACIKLAMALARINI basar.

    Eleme SAYIYLA degil ICERIKLE yapiliyor ve bu cikti o karar icin. 2026-08-20
    dersi: Roblox 43 adayla en zengin havuzdu ama en begenilen klipleri fail
    degil skit'ti; Valorant/CS2 ise oyun okur yazarligi istiyordu. Sayiya
    bakilsaydi ucu de eklenecekti."""
    used = dr.load_used_clips()
    ms = dr.max_candidate_seconds(dr.CLIP_COUNT)
    try:
        vids = discovery.search_by_hashtags(cfg["hashtags"], pages_per_tag=PAGES)
    except Exception as e:
        print(f"    ornek alinamadi: {str(e)[:60]}")
        return
    ger = cfg.get("required") or ()
    uygun = [v for v in vids
             if discovery.video_link(v) not in used
             and (v.get("digg_count") or 0) >= dr.MIN_DIGG_COUNT
             and dr.MIN_CLIP_SECONDS <= (v.get("duration") or 0) <= ms
             and (not ger or any(w in (v.get("title") or "").lower() for w in ger))
             and not dr.is_reaction_title(v.get("title") or "")
             and not dr.uygunsuz_baslik(v.get("title") or "")]
    uygun.sort(key=lambda v: -(v.get("digg_count") or 0))
    sesli = sum(1 for v in uygun if discovery.is_original_sound(v))
    print(f"    orijinal ses: {sesli}/{len(uygun)}")
    for v in uygun[:adet]:
        print(f"    {v.get('duration')}s {(v.get('digg_count') or 0)//1000:>5}k  "
              f"{(v.get('title') or '')[:95]}")


if __name__ == "__main__":
    if "--adaylar" in sys.argv:
        print("ADAY KONULAR olculuyor (eklenmeden once)\n")
        sonuc = tara(ADAY_KONULAR)
        gecen = [k for k, v in sonuc.items() if v >= dr.CLIP_COUNT]
        print(f"\nesigi gecen ({dr.CLIP_COUNT}+ aday): {len(gecen)}/{len(sonuc)}")
        for k in sorted(gecen, key=lambda k: -sonuc[k]):
            print(f"   {sonuc[k]:>3} aday  {k}")
        if "--ornekler" in sys.argv and gecen:
            print("\n" + "=" * 70)
            print("ICERIK ORNEKLERI — eleme buradan yapilacak, sayidan degil")
            print("=" * 70)
            by_name = {c["topic"]: c for c in ADAY_KONULAR}
            for k in sorted(gecen, key=lambda k: -sonuc[k]):
                print(f"\n--- {k} ({sonuc[k]} aday) ---")
                ornekleri_bas(by_name[k])
                time.sleep(ISTEKLER_ARASI)
    else:
        print("MEVCUT konular olculuyor\n")
        tara(dr.RANKING_TOPICS, kaydet=True)
