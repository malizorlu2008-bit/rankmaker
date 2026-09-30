"""Daily orchestration, no Claude agent required at runtime (see discovery.py
docstring for why). Picks today's Ranking topic (rotating, avoids repeats via
pipeline/state/covered_topics.json) + the Slime/satisfying format, finds
clips, downloads, renders, and uploads both to YouTube as private videos
scheduled (status.publishAt) for today's 19:00 and 20:00 Europe/Istanbul.

Entry point for the GitHub Actions workflow: `python3 -m pipeline.daily_run`.
"""
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from pipeline import (bank, comment, discovery, labeler, motion,  # noqa: E402
                      quality, render, reserve, tiktok, visibility)

STATE_PATH = os.path.join(REPO_ROOT, "pipeline", "state", "covered_topics.json")
USED_CLIPS_PATH = os.path.join(REPO_ROOT, "pipeline", "state", "used_clips.json")
# 500'den 20000'e cikarildi (kullanici karari 2026-08-11: bir klip SADECE BIR
# KEZ kullanilsin). 500 sinirinda gunde ~10 klip ile ~50 gun sonra en eski
# linkler listeden dusuyor ve ayni klip "hic kullanilmamis" sayilip tekrar
# secilebiliyordu. 20000 link ~1.5 MB JSON ve ~5 yillik kullanima yetiyor.
USED_CLIPS_CAP = 20000
SUMMARY_NAME = "summary.json"
UPLOAD_SCRIPT = os.path.join(REPO_ROOT, ".credentials", "upload.py")
TOKEN_PATH = os.path.join(REPO_ROOT, ".credentials", "token.json")

REACTION_KEYWORDS = ("react", "reacting", "reaction", "watching", "responds", "responding")

# Siralama satirlarinda etiket YOK, sadece 1-5 numaralari gosteriliyor
# (kullanici karari 2026-08-02): klibin icinde ne oldugunu anlayan bir sistem
# olmadan uretilen etiketler ("Faceplant", "Wipeout") icerikle eslesmiyordu.

# Sifatlar sonege gore ayri havuzlarda. "Fails" sonegiyle olumlu bir sifat
# celisiyor ("Most Satisfying Parachute Fails" — kullanici bildirdi 2026-08-09),
# "Moments" sonegiyle ise olumsuz sifat tuhaf kaciyor. Yeni sifat eklerken
# testi: "<sifat> ... <sonek>" kulaga mantikli geliyor mu?
ADJECTIVES_FAIL = [
    "Best", "Funniest", "Craziest", "Worst", "Most Painful",
    "Weirdest", "Most Awkward", "Most Savage", "Wildest",
    "Most Embarrassing", "Most Epic", "Unluckiest",
]
ADJECTIVES_MOMENT = [
    "Funniest", "Craziest", "Wildest", "Most Unexpected", "Most Awkward",
    "Weirdest", "Most Savage", "Most Epic", "Most Unreal",
]

# Konu listesi 2026-08-09'da tamamen degistirildi. Eski liste jenerik
# kategorilerdi (Waterpark, Gym, Wedding, Skiing, Camping, Fishing, Cooking,
# Golf, Bowling, Job Interview) ve hepsi 1-5 goruntulemede kaldi. Ayni formatta
# milyonlar alan kanallar (@whorankedit ve "Ranking Worst ASMR Fails"
# videolari) belirli viral mikro-trendleri isliyor: Scaring Dogs, Invisible
# Prank, Dogs Watching TV, Ring Camera, Whisper Bark, Cat Band, ASMR Fails,
# Unboxing Fails. Yeni konu eklerken kriter: TikTok'ta kendi basina aranan,
# spesifik, yeni bir sey mi — yoksa genis bir kategori mi? Genis kategori ekleme.
#
# 2026-08-13'te hepsinin hashtag verimi olculdu; "Scaring Dog" (0 aday) ve
# "Dog Watching TV" (1), "Wake Up Prank" (4) ve "Escalator" (4) 100k+ begeni
# suzgecini gecemedigi icin cikarildi — o gun Ranking videosu bu yuzden atlandi.
# Olculen verim (aday sayisi): ASMR 76, Gender Reveal 28, Raccoon/Waterslide 18,
# Husky/Unboxing 16, Zoomies 15, Cat Knocking 11, Treadmill 10, Ring Camera/Ice
# Rink 9, Water Prank 8, Jump Scare/Invisible Prank 5-6 (sinirda ama yeterli).
# Yeni konu eklerken verimini ONCE olc.
#
# "title_topic": BASLIKTA gorunecek ad. Yoksa "topic" kullanilir. Ayri olmasinin
# sebebi: hashtag'ler konu adindan genis olabiliyor, o zaman baslik klipte
# olmayan bir sey vaat ediyor. 2026-08-13: "Raccoon Stealing Moments" cikti ama
# hashtag'ler #raccoon/#trashpanda oldugu icin kliplerin cogu calma degildi
# (kullanici bildirdi). Kural: title_topic, hashtag'lerin HEPSININ garanti
# ettigi ortak sey olmali — daha fazlasi degil.
#
# "queries": tikwm aramasina gidecek ifadeler. Konu adindan otomatik uretmiyoruz
# cunku "Scaring Dog fail" gibi anlamsiz aramalar cikiyordu.
RANKING_TOPICS = [
    # Futbol, kanalin en iyi cikisini yapan konu: 2026-08-15'te yeniden
    # yayinlanan "Ranking The Funniest Soccer Fails" birkac saatte 2.495
    # izlenmeye ulasti (o gunun digeri 777'de kaldi).
    # DIKKAT — etiketler BILEREK amator/saha kenari: #messi, #ronaldo gibi
    # oyuncu etiketleri neredeyse tamamen MAC YAYINI goruntusu getiriyor
    # (olcum 2026-08-15: Premier League, Dunya Kupasi klipleri). Yayin
    # goruntusu GORUNTU BAZLI Content ID iddiasi demek; bizim susturma
    # mekanizmamiz sadece SESI koruyor, goruntu iddiasina caresi yok.
    # Bu konu funny etiketleri tasidigi halde sonegi BILEREK "Fails": kanalin
    # olculen en iyi videosu "Ranking The Funniest Soccer Fails" basligiyla
    # 22.469 izlenme aldi (kanal ortalamasi ~1.000). Diger konularda etiket
    # genisleyince baslik da genisletildi; burada kanit tersini soyluyor,
    # o yuzden calisana dokunulmuyor.
    {"topic": "Football Fail", "required": ('football', 'soccer', 'goalkeeper', 'keeper', 'penalty'),
     "title_topic": "Football", "hashtags": ["goalkeeperfail", "footballfunnymoments", "footballfail", "footballfunny"],
     "suffix": "Fails",
     "queries": ["football fail funny", "goalkeeper fail", "amateur football funny moment"]},
    # 2026-08-17'de olculerek eklendi (pipeline/topic_scan.py --adaylar).
    # 20 aday konu tarandi, 5'i esigi gecti; parantezdeki sayi o taramadaki
    # uygun aday sayisi (2 sayfa/hashtag ile — uretimde 4 sayfa, yani gercek
    # verim bunun ustunde). Elenenler kayda deger: Truck 1, Ladder 1, Bike 1,
    # Penalty 1, Monkey 1, Dog Catching Treat 0 — olcmeden eklenseydi bu
    # konular videoyu atlatirdi.
    {"topic": "Horse Moments", "title_topic": "Horse", "suffix": "Moments",  # (16)
     "required": ('horse', 'pony', 'riding'),
     "hashtags": ["horsefail", "funnyhorse", "horsesoftiktok"],
     "queries": ["horse fail funny", "horse riding fail", "funny horse moment"]},
    {"topic": "Cat Jump Moments", "title_topic": "Cat Jump", "suffix": "Moments",  # (14)
     "required": ('cat', 'kitten', 'jump'),
     "hashtags": ["catfail", "catjumpfail", "funnycatvideos"],
     "queries": ["cat jump fail", "cat misses jump", "funny cat fail"]},
    {"topic": "Snow Fail", "title_topic": "Snow", "suffix": "Fails",          # (12)
     "required": ('snow', 'ice', 'sled', 'ski'),
     "hashtags": ["snowfail", "sledfail", "skifail"],
     "queries": ["snow fail funny", "sledding fail", "ski fail"]},
    {"topic": "Goat Screaming", "title_topic": "Goat", "suffix": "Moments",   # (6)
     "required": ('goat', 'screaming'),
     "hashtags": ["screaminggoat", "funnygoat", "goatsoftiktok"],
     "queries": ["screaming goat", "funny goat moment", "goat yelling"]},
    {"topic": "Parrot Talking", "title_topic": "Parrot", "suffix": "Moments", # (5)
     "required": ('parrot', 'bird', 'cockatoo'),
     "hashtags": ["parrottalking", "talkingparrot", "funnyparrot"],
     "queries": ["parrot talking funny", "talking parrot", "parrot mimics"]},
    # OYUN KONULARI (2026-08-20). Kullanici iki kez gaming istedi; havuz
    # olculdu ve yediden UCU alindi. Parantezdeki sayi 4 sayfalik olcum.
    #
    # Eleme SAYIYLA degil ICERIKLE yapildi, cunku sayi yaniltti: Roblox 43
    # adayla en zengin konuydu ama en begenilen klipleri fail degil skit/
    # meme'di ("I will never understand women", "see you tomorrow") ve kubik
    # gorsel oyunu bilmeyene hicbir sey anlatmiyor. Gamer Rage (20) cogunlukla
    # konusan-kafa yorum videosu; Valorant (24) ve CS2 (32) ise oyun okur
    # yazarligi istiyor ("buy a sheriff on an eco") — kanalin izleyicisi genel
    # fail izleyicisi, espriyi anlamaz. Alinan ucu, oynamayanin da anladigi
    # gorsel komedi veriyor.
    #
    # "Gaming'de klipler susturulur" endisesi OLCULDU ve yanlis cikti:
    # adaylarin %82-96'si orijinal sesli (GTA 27/33, Fortnite 32/39,
    # Minecraft 26/29), yani MUTED_PENALTY bu konularda isirmiyor.
    #
    # DIKKAT: bu konular "gaming" grubunda. Gunun 2. ve 4. slotu SADECE bu
    # gruptan uretiliyor (bkz. SLOT_GRUPLARI), digerleri bu gruba hic
    # bakmiyor — yani gunluk 2 gaming + 2 gaming-disi dagilimi garanti.
    {"topic": "Fortnite Moments", "title_topic": "Fortnite", "suffix": "Moments",  # (39)
     "group": "gaming",
     "required": ('fortnite', 'royale', 'victory', 'zero build'),
     "hashtags": ["fortnitefunny", "fortniteclips", "fortnitemoments", "fortnitefails"],
     "queries": ["fortnite funny moment", "fortnite funny clip", "fortnite fail funny"]},
    {"topic": "GTA Moments", "title_topic": "GTA", "suffix": "Moments",           # (33)
     "group": "gaming",
     "required": ('gta', 'grand theft', 'los santos', 'rp'),
     "hashtags": ["gta5funny", "gtaonline", "gtarp", "gtafails"],
     "queries": ["gta 5 funny moment", "gta online funny", "gta funny clip"]},
    {"topic": "Minecraft Moments", "title_topic": "Minecraft", "suffix": "Moments",  # (29)
     "group": "gaming",
     "required": ('minecraft', 'creeper', 'mine', 'hardcore'),
     "hashtags": ["minecraftfunny", "minecraftmoments", "minecraftclips", "minecraftfails"],
     "queries": ["minecraft funny moment", "minecraft funny clip", "minecraft fail funny"]},
    {"topic": "Roblox Moments", "title_topic": "Roblox", "suffix": "Moments",     # (43)
     "group": "gaming",
     "required": ('roblox', 'obby', 'brookhaven'),
     "hashtags": ["robloxfunny", "robloxmoments", "robloxedit", "robloxfails"],
     "queries": ["roblox funny moment", "roblox funny clip", "roblox meme"]},
    # Ikinci gaming dalgasi (2026-08-20 aksami olculdu). Gunde IKI gaming
    # videosu cikiyor ama grupta sadece dort konu vardi, yani her konu iki
    # gunde bir tekrar ediyordu; bu dortlu tekrar araligini dorde cikariyor.
    #
    # EA FC (12 aday) OLCULDU VE REDDEDILDI: #fifa ve #worldcup etiketleri
    # neredeyse tamamen MAC YAYINI getiriyor ("FIFA World Cup Closing
    # Performances", "Erling Haaland ha recibido el premio"). Yayin goruntusu
    # GORUNTU BAZLI Content ID iddiasi demek ve per-klip ses susturmanin buna
    # caresi yok — ayni gerekceyle #messi/#ronaldo da 2026-08-15'te elenmisti.
    {"topic": "Call Of Duty Moments", "title_topic": "Call Of Duty", "suffix": "Moments",  # (25)
     "group": "gaming",
     "required": ('warzone', 'call of duty', 'cod', 'killcam'),
     "hashtags": ["callofduty", "warzoneclips", "codclips", "codfunny"],
     "queries": ["call of duty funny moment", "warzone funny clip", "cod proximity chat"]},
    {"topic": "Rocket League Moments", "title_topic": "Rocket League", "suffix": "Moments",  # (22)
     "group": "gaming",
     "required": ('rocket league', 'rl', 'aerial', 'goal'),
     "hashtags": ["rocketleague", "rocketleagueclips", "rocketleaguefunny"],
     "queries": ["rocket league funny moment", "rocket league clip", "rocket league fail"]},
    {"topic": "Rage Quit", "title_topic": "Rage Quit", "suffix": "Moments",       # (18)
     "group": "gaming",
     # required'dan 'gamer' ve 'gaming' BILEREK cikarildi: olcumde bu iki
     # kelime konusan-kafa videolarini iceri aliyordu ("GAMING IS MY
     # THERAPY!"). Kalan kelimeler fiziksel tepkiyi tarif ediyor.
     "required": ('rage', 'quit', 'controller', 'keyboard', 'broke', 'threw'),
     "hashtags": ["ragequit", "controllerthrow", "gamerrage", "gamingfails"],
     "queries": ["rage quit funny", "controller throw rage", "gamer rage moment"]},
    {"topic": "Horror Game Reaction", "title_topic": "Horror Game", "suffix": "Moments",  # (16)
     "group": "gaming",
     "required": ('horror', 'scary', 'jumpscare', 'scared'),
     "hashtags": ["horrorgame", "jumpscare", "horrorgaming", "scarygame"],
     "queries": ["horror game reaction", "horror game jumpscare", "scary game funny"]},
    # UCUNCU GAMING DALGASI (2026-09-05, runner'da olculdu). Parantezdeki sayi
    # olcum. Kullanici "valorant cs de istiyordum funny moments gibi" dedi ve
    # HAKLI CIKTI: ayni iki oyun 2026-08-20'de esports etiketleriyle (
    # valorantclips/valorantace) 24 ve 32 vermisti, komik-an etiketleriyle 46
    # ve 68 verdi — hem daha zengin hem icerik olarak dogru.
    #
    # ELENENLER (sayiyla gecti, ICERIKLE elendi — bu projenin tekrar eden dersi):
    #   Elden Ring Deaths (27): klipler komik olum degil #eldenringedit hype
    #     montaji, ustelik "Part 4"/"Pt 2" seri icerigi.
    #   Phasmophobia (26): en begenilenler oyunun KENDI gelistirici hesabindan
    #     tanitim ("head on over to our blog", "our game").
    #   Among Us (24): gelistirici duyurusu + #amongusanimation + rehber
    #     videosu, ustelik 2021 tarihli.
    {"topic": "CS2 Moments", "title_topic": "CS2", "suffix": "Moments",        # (68)
     "group": "gaming",
     "required": ('cs2', 'csgo', 'counter strike'),
     "hashtags": ["cs2funny", "cs2fails", "csgofunny", "cs2memes"],
     "queries": ["cs2 funny moment", "csgo funny clip", "cs2 fail"]},
    {"topic": "Valorant Moments", "title_topic": "Valorant", "suffix": "Moments",  # (46)
     "group": "gaming",
     "required": ('valorant', 'jett', 'sage', 'spike'),
     "hashtags": ["valorantfunny", "valorantfails", "valorantmoments", "valoranttiktok"],
     "queries": ["valorant funny moment", "valorant fail", "valorant funny clip"]},
    {"topic": "Fall Guys", "title_topic": "Fall Guys", "suffix": "Moments",    # (16)
     "group": "gaming",
     "required": ('fall guys', 'fallguys'),
     "hashtags": ["fallguys", "fallguysclips", "fallguysfunny"],
     "queries": ["fall guys funny moment", "fall guys fail", "fall guys clip"]},
    {"topic": "Lethal Company", "title_topic": "Lethal Company", "suffix": "Moments",  # (13)
     "group": "gaming",
     "required": ('lethal company', 'lethal'),
     "hashtags": ["lethalcompany", "lethalcompanyclips", "lethalcompanyfunny"],
     "queries": ["lethal company funny", "lethal company clip", "lethal company moment"]},

    # GAMING DISI DALGA (2026-09-05, hepsi olculdu). Hepsi hayvan ya da fiziksel
    # komedi — kanalin kanitlanmis kazananlariyla (Horse, Football, Goat,
    # Parrot) ayni aile. Esigi ancak geçenler (Otter 8, Goose 5, Zoo 5,
    # Surfing 5) BILEREK alinmadi: havuz derinligi lazim, sinirdaki konu birkac
    # gunde kuruyor. 5'in altinda kalanlar: Seagull 3, Squirrel 3, Elephant 4,
    # Dog Bath 2, Karaoke 1, Proposal 4, Bowling 3, Shopping Cart 1, Pool 2,
    # BBQ 4, Delivery 4, Truck 3, Ladder 1, Forklift 3.
    {"topic": "Puppy Moments", "title_topic": "Puppy", "suffix": "Moments",    # (39)
     "required": ('puppy', 'pup'),
     "hashtags": ["puppyfail", "funnypuppy", "puppytiktok"],
     "queries": ["puppy funny moment", "puppy fail", "funny puppy"]},
    {"topic": "Golf Fail", "title_topic": "Golf", "suffix": "Fails",           # (29)
     "required": ('golf', 'swing', 'tee'),
     "hashtags": ["golffail", "golffails", "funnygolf"],
     "queries": ["golf fail funny", "golf swing fail", "funny golf"]},
    {"topic": "Toddler And Dog", "title_topic": "Toddler And Dog", "suffix": "Moments",  # (26)
     "required": ('toddler', 'baby', 'dog'),
     "hashtags": ["toddleranddog", "babyanddog", "kidsanddogs"],
     "queries": ["toddler and dog funny", "baby and dog", "kids and dogs funny"]},
    {"topic": "Cow Moments", "title_topic": "Cow", "suffix": "Moments",        # (21)
     "required": ('cow', 'calf', 'cattle'),
     "hashtags": ["cowsoftiktok", "funnycow", "cowtok"],
     "queries": ["funny cow", "cow moment", "cows being funny"]},
    {"topic": "Monkey Stealing", "title_topic": "Monkey", "suffix": "Moments", # (21)
     "required": ('monkey', 'macaque', 'ape'),
     "hashtags": ["monkeystealing", "funnymonkey", "monkeytiktok"],
     "queries": ["monkey stealing", "funny monkey", "monkey moment"]},
    {"topic": "Jet Ski Fail", "title_topic": "Jet Ski", "suffix": "Fails",     # (19)
     "required": ('jet ski', 'jetski', 'wave runner'),
     "hashtags": ["jetskifail", "jetski", "jetskifails"],
     "queries": ["jet ski fail", "jetski fail funny", "jet ski crash"]},
    {"topic": "Capybara", "title_topic": "Capybara", "suffix": "Moments",      # (18)
     "required": ('capybara', 'capy'),
     "hashtags": ["capybara", "capybaratiktok", "funnycapybara"],
     "queries": ["funny capybara", "capybara moment", "capybara tiktok"]},
    {"topic": "Cake Fail", "title_topic": "Cake", "suffix": "Fails",           # (18)
     "required": ('cake', 'baking', 'frosting'),
     "hashtags": ["cakefail", "bakingfail", "cakefails"],
     "queries": ["cake fail funny", "baking fail", "cake decorating fail"]},
    {"topic": "Scared Cat", "title_topic": "Scared Cat", "suffix": "Moments",  # (17)
     "required": ('cat', 'cucumber', 'scared'),
     "hashtags": ["catvscucumber", "scaredcat", "funnycatsoftiktok"],
     "queries": ["cat scared funny", "cat vs cucumber", "scared cat jump"]},
    {"topic": "Treadmill Fail", "title_topic": "Treadmill", "suffix": "Fails", # (16)
     "required": ('treadmill', 'gym', 'running'),
     "hashtags": ["treadmillfail", "gymfail", "treadmillfails"],
     "queries": ["treadmill fail", "gym fail funny", "treadmill accident"]},
    {"topic": "Bike Fail", "title_topic": "Bike", "suffix": "Fails",           # (14)
     "required": ('bike', 'bicycle', 'cycling'),
     "hashtags": ["bikefail", "bicyclefail", "bmxfail"],
     "queries": ["bike fail funny", "bicycle fail", "bmx fail"]},
    {"topic": "Sheep Moments", "title_topic": "Sheep", "suffix": "Moments",    # (13)
     "required": ('sheep', 'lamb', 'ram'),
     "hashtags": ["sheep", "sheepoftiktok", "funnysheep"],
     "queries": ["funny sheep", "sheep moment", "sheep being funny"]},
    {"topic": "Snowboard Fail", "title_topic": "Snowboard", "suffix": "Fails", # (13)
     "required": ('snowboard', 'board', 'slope'),
     "hashtags": ["snowboardfail", "snowboardfails", "funnysnowboard"],
     "queries": ["snowboard fail", "snowboarding fail funny", "snowboard crash"]},
    # Bu dordu ilk olcumde zayif gorunmustu; tam sayfa (uretimdeki) taramayla
    # esigi rahat gectiler. Parantezdeki sayi 4 sayfalik olcum.
    {"topic": "Skate Fail", "title_topic": "Skateboard", "suffix": "Fails",   # (14)
     "required": ('skate', 'skateboard', 'board'),
     "hashtags": ["skatefail", "skateboardfail", "skatefails"],
     "queries": ["skateboard fail", "skate fail funny", "skater falls"]},
    {"topic": "Slip And Slide", "title_topic": "Slip And Slide", "suffix": "Fails",  # (9)
     "required": ('slide', 'slip', 'water'),
     "hashtags": ["slipandslide", "waterslidefail", "slipnslide"],
     "queries": ["slip and slide fail", "water slide fail", "backyard slide fail"]},
    {"topic": "Scare Cam", "title_topic": "Scare", "suffix": "Moments",      # (7)
     "required": ('scare', 'prank', 'jump'),
     "hashtags": ["scarecam", "scareprank", "jumpscareprank"],
     "queries": ["scare cam reaction", "scare prank funny", "jump scare prank"]},
    # Sonek "Fails" degil: havuzu genisletmek icin funny etiketler eklendi ve
    # artik her klibin bir fail oldugu garanti degil. Kural (kanalin en cok
    # hata ureten kurali): baslik, kliplerin GARANTI ettiginden fazlasini vaat
    # edemez — etiket genisliyorsa baslik da genisler.
    {"topic": "Trampoline Moments", "title_topic": "Trampoline", "suffix": "Moments",  # (6)
     "required": ('trampoline', 'jump'),
     "hashtags": ["trampolinefail", "trampolinefails", "backyardfail",
                  "funnytrampoline", "trampolinefun"],
     "queries": ["trampoline fail", "trampoline fails funny", "backyard trampoline"]},
    {"topic": "Cat Knocking Things", "required": ('cat', 'kitten', 'kitty'), "title_topic": "Cat", "hashtags": ["catknockingthingsover", "catsbeingjerks", "funnycats"], "suffix": "Moments",
     "queries": ["cat knocking things over", "cat pushing stuff off", "cat chaos"]},
    {"topic": "Husky Talking", "required": ('husky',), "hashtags": ["huskytalking", "huskyscreaming"], "suffix": "Moments",
     "queries": ["husky talking back", "husky argument", "husky screaming"]},
    {"topic": "Raccoon Stealing", "required": ('raccoon', 'trash panda'), "title_topic": "Raccoon", "hashtags": ["raccoonsoftiktok", "trashpanda", "raccoon"], "suffix": "Moments",
     "queries": ["raccoon stealing food", "raccoon caught stealing", "raccoon thief"]},
    {"topic": "Dog Zoomies", "title_topic": "Zoomies", "required": ('zoomie',), "hashtags": ["dogzoomies", "zoomies", "puppyzoomies"], "suffix": "Moments",
     "queries": ["dog zoomies", "puppy zoomies crazy", "dog running wild indoors"]},
    {"topic": "Ring Camera", "required": ('ring', 'doorbell', 'camera'), "hashtags": ["ringcamera", "ringdoorbell", "securitycamera"], "suffix": "Moments",
     "queries": ["ring camera funny", "doorbell camera caught", "ring doorbell funny moment"]},
    {"topic": "Invisible Prank", "required": ('invisible',), "hashtags": ["invisiblechallenge", "invisibleprank"], "suffix": "Moments",
     "queries": ["invisible challenge prank dog", "invisible prank reaction", "invisible challenge pet"]},
    {"topic": "Water Prank", "required": ('water',), "hashtags": ["waterprank", "icewaterchallenge", "waterballoonprank"], "suffix": "Moments",
     "queries": ["water prank reaction", "ice water prank", "water bucket prank"]},
    {"topic": "Jump Scare", "required": ('scare',), "hashtags": ["jumpscareprank", "scareprank", "jumpscare"], "suffix": "Moments",
     "queries": ["jump scare prank reaction", "scaring my boyfriend", "corner jump scare"]},
    {"topic": "Gender Reveal", "required": ('reveal',), "hashtags": ["genderrevealfail", "revealgonewrong"], "suffix": "Fails",
     "queries": ["gender reveal fail", "gender reveal gone wrong", "gender reveal disaster"]},
    {"topic": "Unboxing", "required": ('unboxing', 'package', 'parcel'),
     "hashtags": ["unboxing", "unboxingfail", "packagefail", "unboxingfunny", "unboxingasmr"],
     "suffix": "Moments",
     "queries": ["unboxing fail", "unboxing gone wrong", "package opening fail"]},
    # ASMR KALDIRILDI (2026-08-20). Tek etiketi #asmrfail idi ve iki ayri
    # olcumde de 0 aday verdi; secildiginde konu denemelerinden
    # birini bosa harciyordu. Duzgun cercevesi ("Most Satisfying ASMR
    # Moments") ise 2026-08-17'de olculerek emekliye ayrilan satisfying
    # formatinin ta kendisi — en iyi satisfying videosu 972 izlenmede
    # kalmisti. Geri istenirse konu degil FORMAT kararidir.
    {"topic": "Waterslide", "required": ('slide', 'waterpark', 'water park'), "hashtags": ["waterslidefail", "waterslide"], "suffix": "Moments",
     "queries": ["waterslide fail", "waterslide gone wrong", "water slide accident"]},
    {"topic": "Animals Being Derps", "required": ('animal', 'dog', 'cat', 'pet'), "title_topic": "Animal", "hashtags": ["animalsbeingderps", "funnyanimals", "animalsoftiktok"], "suffix": "Moments",
     "queries": ["funny animal moments", "animals being derps", "funny pets"]},
    {"topic": "Wedding", "required": ('wedding', 'bride', 'groom'), "hashtags": ["weddingfail", "weddingfails", "weddingtiktok"], "suffix": "Fails",
     "queries": ["wedding fail", "wedding gone wrong", "bride fail"]},
    {"topic": "Ice Rink", "required": ('skat', 'ice rink'), "title_topic": "Ice Skating", "hashtags": ["iceskatingfail", "iceskating", "icerink"], "suffix": "Moments",
     "queries": ["ice skating fail", "ice rink fall", "first time ice skating fail"]},
]

# Basligin altina giren merak satiri. Ayni format kanallari bunu grafigin
# icine koyuyor: "( The last one is hilarious )", "(Wait For Last)".
HOOK_LINES = [
    "(Wait for #1)",
    "(The last one is unreal)",
    "(#1 got me)",
    "(Watch till the end)",
    "(#1 is the worst)",
]

# YOUTUBE BASLIGI = MERAK TUZAGI, EKRAN BASLIGI = KONU (2026-08-17 testi).
# Neden: RankZilla (2,85M abone, 6,8 milyar izlenme) 268 videosunun 222'sinde
# YouTube basligi olarak tek bir kanca cumlesi kullaniyor ("DON'T CHECK THE
# SOUND"), konuyu ise videonun ICINDEKI baslik kartinda veriyor. Kanalin
# gecmisi bu tercihi destekliyor: ilk aylarinda bizimkiyle ayni tarz aciklayici
# baslik kullaniyordu ve videolari 220-500 bin izlenmede kaliyordu; 25 Haziran
# 2025'te kanca basliga gecti ve milyonlara cikti (en iyisi 403 milyon).
# Ekran basligi DEGISMIYOR — konu orada yaziyor, aciklamada da yaziyor, yani
# arama tarafinda bilgi kaybi yok. Tek degisen YouTube baslik alani.
# Geri almak icin: YOUTUBE_HOOKS listesini bosaltmak yeterli.
# Liste 7'den 28'e cikarildi (2026-08-22). Gunluk video sayisi 2'den 4'e
# cikinca bu liste 1,75 GUNDE bir basa donuyordu ve kanalda birebir ayni
# baslik tekrar tekrar cikiyordu: "DON'T BLINK 😭😭" ve "WAIT FOR #1 😭"
# hem 08-18'de hem 08-21'de yayinlandi. Ayni baslik hem tiklanmayi dusuruyor
# hem de hangi kancanin tuttugunu olcmeyi imkansiz kiliyor.
# 28 baslik = 4 video/gun ile TAM BIR HAFTA tekrarsiz.
# Video sayisi yine artarsa bu liste de buyumeli: kural, listenin gunluk video
# sayisinin en az yedi katini tutmasi.
YOUTUBE_HOOKS = [
    "DON'T BLINK 😭😭",
    "WAIT FOR #1 😭",
    "I CAN'T STOP LAUGHING 😭😭",
    "THE LAST ONE 💀",
    "HOW IS THIS REAL 😭",
    "#1 BROKE ME 😭😭",
    "YOU WON'T BELIEVE #1 👀",
    "#1 IS INSANE 😭",
    "NOT THE LAST ONE 💀",
    "WHO DID THIS 😭😭",
    "I REWATCHED #1 TEN TIMES 👀",
    "THE #1 IS UNREAL 😭",
    "BRO WHAT WAS THAT 💀",
    "#4 CAUGHT ME OFF GUARD 😭",
    "STAY FOR THE END 👀",
    "THIS GOT WORSE EVERY CLIP 😭😭",
    "#1 HAS NO BUSINESS BEING THIS GOOD 💀",
    "MY JAW DROPPED AT #1 😭",
    "NOBODY SAW #1 COMING 👀",
    "IT KEPT GETTING BETTER 😭",
    "#2 IS MY FAVOURITE 💀",
    "WAIT FOR THE LAST ONE 😭😭",
    "HOW DID THEY SURVIVE #1 👀",
    "I WASN'T READY FOR #1 😭",
    "THE ENDING IS WILD 💀",
    "#3 MADE ME PAUSE 😭",
    "THIS IS TOO GOOD 👀",
    "#1 CHANGED EVERYTHING 😭😭",
]


def youtube_basligi(konu_basligi, state=None):
    """YouTube baslik alanina yazilacak metin.

    YOUTUBE_HOOKS bossa konu basligi kullanilir (eski davranis). Doluysa
    siradaki kanca cumlesi — rastgele DEGIL sirayla, cunku rastgele secim ayni
    cumleyi ust uste tekrarlayabiliyor ve tekrar eden baslik tam da kacinmak
    istedigimiz sinyal."""
    if not YOUTUBE_HOOKS:
        return konu_basligi
    if state is None:
        return YOUTUBE_HOOKS[0]
    n = int(state.get("hook_index") or 0)
    state["hook_index"] = (n + 1) % len(YOUTUBE_HOOKS)
    return YOUTUBE_HOOKS[n % len(YOUTUBE_HOOKS)]
# Ikinci video eskiden HER GUN slime'di: 7 video ust uste birebir ayni baslikla
# ("Most Satisfying Slime Moments") yuklendi ve YouTube ayni kanaldan gelen
# tekrar eden baslik/icerigi kisitliyor. Artik "rahatlatici/tatmin edici"
# nisinin populer alt turleri arasinda siralaniyor (kullanici istegi
# 2026-08-09: "sadece slime olmasin").
#
# "required": basligi bu kelimelerden birini icermeyen aday elenir — bu suzgec
# sart, cunku duz "satisfying" aramasi beton dokumu gibi alakasiz videolar
# getiriyor.
SATISFYING_TOPICS = [
    {"topic": "Slime", "hashtags": ["slimeasmr", "satisfyingslime", "slime"], "required": ("slime",),
     "queries": ["satisfying slime asmr", "slime crunchy asmr", "butter slime asmr",
                 "clear slime asmr", "slime poking asmr"],
     "titles": [("Most", "Satisfying", "Slime", "Moments"),
                ("Most", "Relaxing", "Slime", "ASMR"),
                ("Best", "Crunchy", "Slime", "Sounds")]},
    {"topic": "Kinetic Sand", "hashtags": ["kineticsand", "kineticsandasmr", "sandcutting"], "required": ("sand",),
     "queries": ["kinetic sand cutting asmr", "kinetic sand satisfying",
                 "sand cutting asmr satisfying"],
     "titles": [("Most", "Satisfying", "Sand", "Cuts"),
                ("Most", "Relaxing", "Sand", "Cutting ASMR")]},
    {"topic": "Soap Cutting", "hashtags": ["soapcutting", "soapcarving", "drysoapcarving"], "required": ("soap",),
     "queries": ["soap cutting asmr", "dry soap carving asmr", "soap cutting satisfying"],
     "titles": [("Most", "Satisfying", "Soap", "Cutting"),
                ("Best", "Dry", "Soap", "Carving ASMR")]},
    {"topic": "Rug Cleaning", "hashtags": ["rugcleaning", "carpetcleaning", "rugtok"], "required": ("rug", "carpet"),
     "queries": ["rug cleaning asmr satisfying", "carpet cleaning satisfying",
                 "dirty rug cleaning transformation"],
     "titles": [("Most", "Satisfying", "Rug", "Cleaning"),
                ("Most", "Relaxing", "Carpet", "Cleaning ASMR")]},
    {"topic": "Pressure Washing", "hashtags": ["pressurewashing", "powerwashing", "satisfyingcleaning"], "required": ("pressure wash", "power wash", "washing"),
     "queries": ["pressure washing satisfying", "power washing asmr",
                 "pressure wash before after satisfying"],
     "titles": [("Most", "Satisfying", "Pressure", "Washing"),
                ("Best", "Power", "Washing", "Moments")]},
    {"topic": "Paint Mixing", "hashtags": ["paintmixing", "paintmixingasmr", "satisfyingpaint"], "required": ("paint",),
     "queries": ["paint mixing asmr", "satisfying paint mixing",
                 "mixing paint colors asmr"],
     "titles": [("Most", "Satisfying", "Paint", "Mixing"),
                ("Most", "Relaxing", "Paint", "Mixing ASMR")]},
    {"topic": "Cake Decorating", "hashtags": ["cakedecorating", "cakeicing", "cakeasmr"], "required": ("cake", "icing", "frosting"),
     "queries": ["cake decorating satisfying", "cake icing asmr",
                 "frosting cake satisfying"],
     "titles": [("Most", "Satisfying", "Cake", "Decorating"),
                ("Most", "Relaxing", "Cake", "Icing")]},
    {"topic": "Water Beads", "hashtags": ["orbeez", "waterbeads", "orbeezasmr"], "required": ("bead", "orbeez"),
     "queries": ["water beads asmr satisfying", "orbeez asmr", "water bead crunch asmr"],
     "titles": [("Most", "Satisfying", "Water Bead", "Moments"),
                ("Most", "Addictive", "Orbeez", "ASMR")]},
    {"topic": "Floral Foam", "hashtags": ["floralfoam", "floralfoamcrushing", "foamcrushing"], "required": ("foam",),
     "queries": ["floral foam crushing asmr", "floral foam satisfying",
                 "dry floral foam crush"],
     "titles": [("Most", "Satisfying", "Floral Foam", "Crush"),
                ("Best", "Foam", "Crushing", "ASMR")]},
    {"topic": "Chocolate", "hashtags": ["chocolateasmr", "chocolatemaking", "chocolatecake"], "required": ("chocolate",),
     "queries": ["chocolate making asmr satisfying", "chocolate pouring satisfying",
                 "candy making asmr"],
     "titles": [("Most", "Satisfying", "Chocolate", "Moments"),
                ("Most", "Relaxing", "Chocolate", "ASMR")]},
]


# Ikinci video artik TEK nisten degil, tum "tatmin edici" nislerden KARISIK
# 5 klip aliyor (kullanici onerisi 2026-08-13). Gerekcesi dogruluk: baslik
# "Slime" derse kliplerin slime olmasi gerekir ve tek nisin havuzu bazen 5
# klibi doldurmuyordu; baslik "Most Satisfying Videos" olunca karisim durust
# oluyor ve havuz on kat buyuyor.
SATISFYING_MIX_TITLES = [
    ("Most", "Satisfying", "Videos", ""),
    ("Most", "Relaxing", "ASMR", "Moments"),
    ("Oddly", "Satisfying", "Moments", ""),
    ("Most", "Satisfying", "ASMR", "Compilation"),
    ("Best", "Satisfying", "Sounds", ""),
    ("Most", "Addictive", "Satisfying", "Clips"),
]
# Havuzdaki TUM etiketler her calismada taraniyor. Eskiden her nisten birer
# etiket seciliyordu ("karisim karisim olsun" diye); kullanici 2026-08-16'da bu
# sarti kaldirdi: "illa farkli icerikler olacak diye bir zorunluluk yok, 4 tane
# kum 1 tane slime bile olabilir eger videolar guzel ve cok begeniliyse".
# Nis cesitliligi yerine KLIP KALITESI onceligi: havuzun tamami taranir,
# en iyi 5 klip hangi nisten gelirse gelsin alinir.
MIX_HASHTAG_SAMPLE = None
# Tatmin edici nis DOGASI GEREGI uzun cekiliyor: 33 adayin sure dagiliminda
# 25sn altinda 1, 40sn altinda 8 klip var (olcum 2026-08-13). Bu yuzden bir
# donem 3 klip x 15sn kullanildi.
# 3 -> 5 (kullanici karari 2026-08-15): satisfying videosu da Ranking gibi 5
# kliplik olsun ve kisa klipler tercih edilsin. 5 klip, klip basina 10sn demek,
# yani aday klip suresi tavani 37sn'den 25sn'ye iniyor (TRIM_RATIO).
SATISFYING_CLIP_COUNT = 5

# Satisfying tarafinda begeni esigi AYRI ve daha dusuk. Neden: ASMR/tatmin
# edici icerik dogasi geregi uzun. 2026-08-15'te 598 aday olctum; 25 saniyenin
# altinda olanlar 50k+ begenide sadece 4 taneydi, yani 5 kliplik video hic
# kurulamazdi. Esik dusunce ayni havuzda: 30k->6, 20k->8, 10k->14.
# Kullanici 20.000'i secti. Ranking tarafi 50.000'de kaliyor.
# 20.000 -> 2.000 (kullanici karari 2026-08-16). Neden: bu niste kisa klip bol
# ama POPULER kisa klip yok. 773 klip tarandi; sesli + alakali + 12 saniyenin
# altinda olanlar: 20k'da 1 tane, 10k'da 3, 5k'da 4, 2k'da 7, esik yokken 53.
# Kullanici bütünlügü populerlige tercih etti: klipler TAM oynasin, hicbir
# yerinden kesilmesin. Seyredilme orani, klibin begeni sayisindan onemli.
SATISFYING_MIN_DIGG = 2000

# Satisfying klipleri HIC KIRPILMIYOR: aday suresi tavani ile klip basina
# ust sinir ayni sayi. Boylece kabul edilen her klip tam oynuyor.
# Once 25, sonra 40 denendi ve "bastan kes" kurali eklendi; o kural klibin
# BASINI duzeltti ama SONUNU bozdu — 38 saniyelik klibin ilk 10 saniyesi isin
# ortasinda bitiyordu ve kullanici "yine yarida kesilmis" dedi (2026-08-16).
# Tek gercek cozum, videoya sigan klip secmek.
# Toplam sure = kliplerin kendi sureleri toplami (5 x en fazla 12sn = 60sn ust
# sinir, pratikte 40-55sn).
SATISFYING_MAX_SECONDS = 12

# Karisim havuzu ELLE secilmis: sadece "tatmin edici/ASMR" anlamini KENDI
# BASINA tasiyan etiketler. SATISFYING_TOPICS'ten turetmeyi denedim ve
# #chocolatecake, #cakeicing gibi yemek etiketleri iceri sizdi — canli testte
# "Most Satisfying Videos" basligina 5 klipten 4'u YEMEK videosu geldi
# (cikolatali kek tarifi, Costco keki, NYC yemek incelemesi). Buraya etiket
# eklerken testi: bu etiketle gelen HER video tatmin edici/ASMR sayilir mi?
# 2026-08-16'da temizlendi: temizlik/yikama etiketleri (#rugcleaning,
# #carpetcleaning, #pressurewashing, #powerwashing, #satisfyingcleaning) ve
# #chocolateasmr cikarildi. Olcumde bunlar KONUSAN videolar getiriyordu
# ("cleaning advice", "a week of cleaning reality") ve kullanici "satisfying
# olmayanlar var" diye bildirdi. Kalanlar gorsel olarak kendi basina tatmin
# edici olan nisler: slime, kinetik kum, sabun, kopuk, orbeez, boya.
MIX_HASHTAGS = [
    "slimeasmr", "satisfyingslime",
    "kineticsandasmr", "sandcutting",
    "soapcutting", "drysoapcarving",
    "paintmixing", "satisfyingpaint",
    "orbeezasmr", "waterbeads",
    "floralfoamcrushing", "floralfoam",
]

# Karisik modda da alaka kapisi acik kalmasin: baslikta bu kelimelerden biri
# gecmeli. Genel bir baslik ("Most Satisfying Videos") kullaniyoruz ama bu
# "her sey girebilir" demek degil — klip yine bu nislerden biri olmali.
MIX_REQUIRED = (
    "asmr", "satisfying", "slime", "kinetic sand", "soap", "foam", "orbeez",
    "water bead", "paint mix", "sand cut",
)


def satisfying_mix_hashtags():
    """Havuzun tamami (MIX_HASHTAG_SAMPLE None ise).

    Eskiden her nisten birer etiket seciliyordu; artik nis cesitliligi sart
    degil (kullanici karari 2026-08-16) ve asil dert yeterince IYI klip bulmak.
    Havuzun tamamini taramak, ayni gun icinde daha cok kisa + sesli + gercekten
    tatmin edici aday demek."""
    if not MIX_HASHTAG_SAMPLE:
        return list(MIX_HASHTAGS)
    gruplar = [MIX_HASHTAGS[i:i + 2] for i in range(0, len(MIX_HASHTAGS), 2)]
    secilen = random.sample(gruplar, min(MIX_HASHTAG_SAMPLE, len(gruplar)))
    return [random.choice(g) for g in secilen]


def pick_satisfying(state):
    """(konu, baslik_varyanti, yeni_sayac) — sirayla doner, rastgele DEGIL.

    Rastgele secim ayni konuyu/basligi ust uste tekrarlayabilir; tekrar eden
    baslik tam da duzeltmeye calistigimiz sorun. Sayac hem konuyu hem varyanti
    ilerletiyor: ilk tur her konunun 1. basligi, ikinci tur 2. basligi..."""
    n = int(state.get("satisfying_index") or 0)
    topic = SATISFYING_TOPICS[n % len(SATISFYING_TOPICS)]
    variant = topic["titles"][(n // len(SATISFYING_TOPICS)) % len(topic["titles"])]
    return topic, variant, n + 1

# Europe/Istanbul has been UTC+3 year-round (no DST) since 2016.
ISTANBUL_OFFSET = timedelta(hours=3)


def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return json.load(f)
    return {"ranking_topics_used": []}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
        f.write("\n")


def load_used_clips():
    if os.path.exists(USED_CLIPS_PATH):
        with open(USED_CLIPS_PATH) as f:
            return set(json.load(f).get("used_links", []))
    return set()


def save_used_clips(used_links):
    os.makedirs(os.path.dirname(USED_CLIPS_PATH), exist_ok=True)
    trimmed = list(used_links)[-USED_CLIPS_CAP:]
    with open(USED_CLIPS_PATH, "w") as f:
        json.dump({"used_links": trimmed}, f, indent=2, ensure_ascii=False)
        f.write("\n")


def is_reaction_title(title):
    t = (title or "").lower()
    return any(k in t for k in REACTION_KEYWORDS)


# Klip aciklamasinda gecerse o klip HIC alinmaz. Kalite filtreleri
# (pipeline/quality.py) sessizlik/derleme/donuk-kare bakiyor, aciklamanin NEYI
# anlattigina bakmiyor — 2026-08-20'de Rage Quit havuzu olculurken en cok
# begenilen aday "#racist #racistgamer #racism" etiketleriyle geldi ve
# siralama begeniye gore oldugu icin videonun 1 NUMARASI olacakti.
#
# Liste bilerek dar: genel bir kufur suzgeci degil, kanalin altina koyamayacagi
# nefret/istismar temalarini disarida birakiyor. Alt dize eslesmesi kullaniliyor
# (#racistgamer gibi birlesik etiketler de yakalansin diye), bu yuzden masum
# kelimelerin icinde gecmeyecek terimler secildi.
UYGUNSUZ_KELIMELER = (
    "racist", "racism", "nazi", "slur", "homophob", "transphob",
    "sexist", "misogyn", "suicide", "self harm", "selfharm",
)


def uygunsuz_baslik(title):
    t = (title or "").lower()
    return any(k in t for k in UYGUNSUZ_KELIMELER)


# MIN_CLIPS_FOR_VIDEO asagida, CLIP_COUNT tanimlandiktan sonra.
# Yeterli klip bulunamayan konu icin kac farkli konu denenecek. Kullanici karari
# 2026-08-13: "video atlanmasin hicbir sekilde, konu bulsun arayip". Ust sinir
# var cunku her deneme hashtag istekleri + indirme + kalite kontrolu demek.
# Bir slot icin kac farkli konu denenir. 5'ti, 2026-09-05'te kaldirildi:
# kullanici karari "taze klip bul gerekirse 100 tane konu tara". Artik slotun
# grubundaki BUTUN konular denenir; bu sayi sadece sonsuz donguye karsi tavan
# (havuzdan asla fazla konu olamaz).
#
# Neden gerekti: havuz zayifladikca bes deneme yetmiyor, slot ac kaliyor ve
# bankaya/yedege dusuyordu — gunde uc gaming videosunun ve yayinlanan 1
# dakikalik eski re-upload'larin sebebi buydu.
#
# Zaman asimi riski konu sayisini kisarak DEGIL, CI suresini buyuterek
# kapatildi (workflow'da timeout-minutes 170 -> 330). Is zaman asimina
# ugrarsa state commit'i de kaybolur ve ertesi gun ayni slotlar tekrar
# denenir — kacinilan asil zarar bu. Denemeler skora gore inen sirada gittigi
# icin (en_verimlisi) isabetli konu genelde ilk birkac denemede bulunur;
# uzun kuyruk sadece gercekten ac kalan slotta calisir.
def max_topic_attempts():
    return len(RANKING_TOPICS)


# Bu calismada kaynak (tikwm) TAMAMEN erisilemez mi oldu? Yedek kutuphanesi
# artik SADECE bu durumda aciliyor. Once yedek, slot bos kalan HER durumda
# devreye giriyordu; havuz zayifladiginda 1 dakikalik eski re-upload'lar
# yayinlandi (09-02'de "Most Satisfying Slime Moments" 18 izlenme aldi).
# "Havuz zayif" ile "kaynak bloklu" farkli seyler: birincisinde konu taranarak
# taze klip aranir, ikincisinde taranacak hicbir sey yoktur.
KAYNAK_BLOKLU = False


def konu_grubu(topic):
    """Bir konunun grubu ("gaming" ya da None). Listede yoksa gaming disi."""
    for t in RANKING_TOPICS:
        if t["topic"] == topic:
            return t.get("group")
    return None


def bugun_uretilenler(state):
    """BUGUN zaten video yapilmis konularin adlari.

    Ayni konunun ayni gun iki kez cikmasini engelliyor: 2026-09-01'de Roblox,
    09-02'de GTA ikiser kez yayinlandi ve ikisinde de "Part N" etiketi yoktu.
    Farkli GUNLERDEKI tekrar (Part 2, Part 3...) kasitli ve dokunulmuyor —
    RankZilla'nin calisan stratejisi o.
    """
    bugunku = bugun()
    return {k.get("topic") for k in (state.get("uploads") or [])
            if k.get("date") == bugunku and k.get("topic")}


def grup_havuzu(pool, grup):
    """Slotun grubuna uyan konular. grup None ise gaming DISI konular.

    KATI: grubu bos cikarsa TUM HAVUZ DEGIL, bos liste doner. Eskiden
    "kalan or pool" idi ve grup atamasi bir tercih sayiliyordu; ama sessizce
    grup degistirmek tam da kullanicinin sikayet ettigi sey (gunde uc gaming
    videosu). Deneme sayisi 2026-09-05'te sinirsiz hale gelince bu satir
    gercekten erisilebilir oldu: bir grubun butun konulari denenip tukenebilir.
    Grup gercekten tukendiyse slot bos kalir — yedege DUSMEZ (yedek artik
    sadece kaynak tamamen bloklu oldugunda aciliyor).
    """
    if grup is None:
        return [t for t in pool if not t.get("group")]
    return [t for t in pool if t.get("group") == grup]


def pick_ranking_topic(state, exclude=(), forced=None, grup=None,
                       sadece_skor=False):
    """(konu_dict, part) dondurur. part None ya da 2,3,... — "Part 2" mantigi.

    Neden Part 2: ayni format kanallari tutan bir konuyu birakmiyor
    ("Ring Camera" 47 Mn -> Part 2; "Unreal Moments" Part 2-3-4-5-7; "ASMR
    Fails" ucu de 14-39 Mn). Bizim eski mantik bir konuyu ASLA tekrar
    etmiyordu, yani en karli hamle kod duzeyinde yasakliydi.

    Kazanani otomatik secemiyoruz: OAuth token'imizin izni sadece
    youtube.upload, izlenme verisi okuyamiyor. O yuzden iki yol var:
      1) state["part_two_queue"] — elle (ya da ileride bir analiz adimiyla)
         doldurulan konu adlari listesi; doluysa siradaki Part olarak islenir.
      2) Butun konular bir kez islendiginde, en az islenmis konuya Part N.
    """
    exclude = set(exclude)
    # BUGUN zaten cikmis konular da dislaniyor. Bu satir olmadan ayni konu ayni
    # gun iki kez cikabiliyordu (Roblox 09-01, GTA 09-02) — ustelik "Part N"
    # etiketi bile almadan, cunku iki uretim arasinda kayit henuz islenmemis
    # olabiliyor.
    exclude |= bugun_uretilenler(state)
    used_list = list(state.get("ranking_topics_used", []))
    by_name = {t["topic"]: t for t in RANKING_TOPICS}

    pool = [t for t in RANKING_TOPICS if t["topic"] not in exclude]
    pool = grup_havuzu(pool, grup)
    # Kadroyu dolduramayacak kadar zayif konular elenir. 2026-09-05'te olculdu:
    # bu suzgec olmadan ac kalan bir gaming-disi slot EN ZAYIF ON BIR konuyu
    # (skor 1-7) deneyip ancak ondan sonra Football'a (33) ve Horse'a (40)
    # geliyordu — cunku "hic kullanilmamis" konular skora tercih ediliyor ve
    # o konularin hic kullanilmamis olmasinin sebebi zaten hic klip vermemeleri.
    pool = bol_havuz(pool)
    if not pool:
        return None, None
    havuz_adlari = {t["topic"] for t in pool}

    # Elle secilen konu (--topic): eski bir konuyu yeni formatla tekrar
    # uretmek icin. Rotasyonu ve "kullanilmis" kaydini atlar; ayni konu daha
    # once islendiyse baslikta Part N gorunur.
    #
    # DIKKAT: bu kontrol havuz SUZULDUKTEN sonra yapiliyor. Eskiden en basta
    # donuyordu ve grup suzgecini tamamen deliyordu: "--topic 'Fortnite
    # Moments'" gaming DISI bir slota da yerlesebiliyordu.
    if forced and forced not in by_name:
        print(f"  UYARI: '{forced}' diye bir konu yok, rotasyona donuluyor. "
              f"Mevcut konular: {', '.join(sorted(by_name))}")
    elif forced and forced not in havuz_adlari:
        print(f"  UYARI: '{forced}' bu slotun grubuna ({grup or 'gaming disi'}) "
              f"uymuyor ya da bugun zaten cikti — rotasyona donuluyor.")
    elif forced:
        n = used_list.count(forced)
        return by_name[forced], (n + 1 if n else None)

    queue = [n for n in (state.get("part_two_queue") or [])
             if n in by_name and n not in exclude and n in havuz_adlari]
    if queue:
        name = queue[0]
        return by_name[name], used_list.count(name) + 1

    # sadece_skor: ILK deneme basarisiz oldu, artik sadece verim onemli.
    # Tazelik/cesitlilik tercihi (asagidaki "unused" onceligi) sadece ilk
    # denemede uygulanir; ac kalan slotta en cok klip veren konu ne ise o.
    if sadece_skor:
        en_iyi = skora_gore(pool)[0]
        n = used_list.count(en_iyi["topic"])
        return en_iyi, (n + 1 if n else None)

    # Ilk deneme tazeligi tercih eder AMA sadece bolluk icinde. Skoru
    # CLIP_COUNT'un iki katindan az olan taze bir konu, verimi 40 olan
    # kullanilmis bir konudan daha kotu bir ilk deneme: en ucuz deneme hakkini
    # muhtemelen kadroyu dolduramayacak bir konuya harciyor.
    unused = [t for t in pool if t["topic"] not in set(used_list)
              and (topic_scan_skorlari().get(t["topic"]) or 0) >= CLIP_COUNT * 2]
    if unused:
        return en_verimlisi(unused), None

    fewest = min(used_list.count(t["topic"]) for t in pool)
    candidates = [t for t in pool if used_list.count(t["topic"]) == fewest]
    return en_verimlisi(candidates), fewest + 1


def bol_havuz(pool):
    """Son taramada kadroyu doldurabilecek kadar klip veren konular.

    Skoru CLIP_COUNT'un altinda olan konu tanim geregi tam kadro veremez;
    denemek sadece CI suresi harciyor. Bugun sekiz gaming-disi konu bu
    durumda (Trampoline 1, Jump Scare 1, Cat Knocking 2, Invisible Prank 2...).

    OLCULMEMIS konu iceride kalir: yeni eklenen bir konu bir sonraki taramaya
    kadar kilitlenmesin. Hicbir konu bol degilse eski davranisa donulur —
    zayif bir deneme, hic deneme yapmamaktan iyidir.
    """
    skor = topic_scan_skorlari()
    if not skor:
        return pool
    bol = [t for t in pool if skor.get(t["topic"], CLIP_COUNT) >= CLIP_COUNT]
    return bol or pool


def skora_gore(adaylar):
    """En cok klip verenden aza dogru, RASTGELELIK YOK.

    Ac kalan bir slot icin tek onemli sey verim: tazelik/cesitlilik tercihi
    (bkz. en_verimlisi ve "unused" onceligi) ilk denemeye ait, sonrakilere
    degil.
    """
    skor = topic_scan_skorlari()
    return sorted(adaylar, key=lambda t: -(skor.get(t["topic"]) or 0))


def en_verimlisi(adaylar):
    """Son taramada en cok klip veren konuyu secer, skor yoksa rastgele.

    "Trend destegi" burada haber takibi degil: pipeline/topic_scan.py butun
    konularin GUNCEL aday sayisini olcuyor (kac tane taze, 50k+ begenili, kisa
    klip var) ve konu secimi bu skoru kullaniyor. Boylece o hafta bol malzeme
    veren konu one geciyor, kurumus konu geriye dusuyor — kaynagin kendi
    verisiyle, tahminle degil.

    Ilk 3 arasindan rastgele seciliyor: hep en tepedekini almak ayni konuyu
    ust uste tekrarlatir, oysa amac hem taze malzeme hem cesitlilik."""
    if not adaylar:
        return None
    skor = topic_scan_skorlari()
    if not skor:
        return random.choice(adaylar)
    sirali = sorted(adaylar, key=lambda t: -skor.get(t["topic"], 0))
    return random.choice(sirali[:3])


def topic_scan_skorlari():
    try:
        from pipeline import topic_scan
        return topic_scan.skorlar()
    except Exception:
        return {}


def find_ranking_material(state, used_links, forced_topic=None, grup=None):
    """Yeterli klip veren bir Ranking konusu bulana kadar farkli konular dener.

    (topic_cfg, part, items, links, metas, source, tried) doner. Eskiden ilk
    konu yetersizse video ATLANIYORDU — 2026-08-13'te "Scaring Dog" (0 aday) ve
    "Dog Watching TV" (1 aday) yuzunden gunun Ranking videosu cikmadi. Kullanici
    karari: atlama olmasin, konu arayarak bulsun.
    """
    global KAYNAK_BLOKLU
    tried = []
    best = None
    # Kaynak bu calismada zaten tamamen bloklu bulunduysa konu denemenin anlami
    # yok: her deneme discovery.find_videos icinde 60 saniyelik bir tekrar
    # beklemesi demek (SOURCE_RETRY_WAIT) ve blok IP bazli oldugu icin ayni
    # calismada acilmiyor. Sinirsiz denemeyle bu, bloklu bir gunde saatler
    # yiyordu — 2026-09-05 testinde yakalandi.
    if KAYNAK_BLOKLU:
        print("  kaynak bu calismada bloklu bulundu — konu denenmiyor")
        return None, None, [], [], [], "kaynak", tried
    # GRUP GECISI (kullanici karari 2026-09-30): slotun kendi grubundaki TUM
    # konular denenip tam kadro cikmazsa, slot bos kalmasin diye diger gruba
    # geciliyor. 29 Eylul'de gaming havuzu tam kadro veremedi ve 21:00 slotu
    # bos kaldi; gunde 4 video kurali bundan onceliklidir.
    #
    # GECIS TEK YONLU: sadece gaming -> gaming disi. Tersi olsaydi gaming disi
    # bir slot gaming konusuyla dolabilir ve gunde UC gaming videosu cikardi —
    # kullanici tam bundan sikayet etmisti (2026-09-05). Gecis ayrica loglanir,
    # yani grup_havuzu'nun eski sessiz sizintisi geri gelmiyor.
    aktif_grup = grup
    grup_gecildi = False
    for attempt in range(max_topic_attempts() * 2):
        topic_cfg, part = pick_ranking_topic(
            state, exclude=tried, forced=forced_topic if attempt == 0 else None,
            grup=aktif_grup, sadece_skor=(attempt > 0))
        if topic_cfg is None:
            if grup == "gaming" and not grup_gecildi:
                grup_gecildi = True
                aktif_grup = None
                print(f"  gaming havuzunda ({len(tried)} konu denendi) tam kadro "
                      f"cikmadi — slot gaming disi konuyla doldurulmaya calisiliyor")
                continue
            break
        topic = topic_cfg["topic"]
        tried.append(topic)
        t_low = topic.lower()
        queries = list(topic_cfg["queries"]) + [f"{t_low} funny", f"funny {t_low}"]
        if attempt:
            print(f"--- {attempt + 1}. konu denemesi: {topic} ---")
        try:
            items, links, metas, source = gather_clips(
                queries, used_links, hashtags=topic_cfg["hashtags"],
                must_include=topic_cfg.get("required"),
                kind="ranking", topic=topic,
                title_topic=topic_cfg.get("title_topic") or topic,
                suffix=topic_cfg.get("suffix"))
        except discovery.SourceUnavailable as e:
            print(f"  kaynak erisilemiyor ({e}) — konu denemeyi birakiyorum")
            KAYNAK_BLOKLU = True
            items, links, metas, source = [], [], [], "kaynak"
            if best is None:
                best = (topic_cfg, part, items, links, metas, source, list(tried))
            break
        # En cok klip veren denemeyi sakla: hicbiri yetmezse en iyisiyle devam.
        if best is None or len(items) > len(best[2]):
            best = (topic_cfg, part, items, links, metas, source, list(tried))
        # TAM video arayisi: 3 klip minimum ama hedef CLIP_COUNT. Eskiden 3'te
        # duruyordu ve 2026-08-13'te Kinetic Sand 3 klipte kalip 32 saniyelik
        # ince bir video cikti (kullanici bildirdi). Baska konu tam video
        # verebiliyorsa onu tercih ediyoruz.
        if len(items) >= CLIP_COUNT:
            return topic_cfg, part, items, links, metas, source, list(tried)
        print(f"  '{topic}' {len(items)}/{CLIP_COUNT} klip verdi, "
              f"tam video icin baska konu deneniyor")
        # KALDIRILDI (2026-09-29): burada "en iyi uc konu ucer klip veremediyse
        # birak" diye bir erken cikis vardi. Gerekcesi "devam etmek CI suresi
        # harcamaktan baska bir sey degil"di — depo 2026-09-28'de public
        # oldugundan Actions dakikasi artik ucretsiz ve sinirsiz, yani o gerekce
        # yok. Kullanici kurali zaten bunun tersiydi: "sinir koyma, bulana kadar
        # dene / gerekirse 100 tane konu tara".
        #
        # Somut bedeli olctuk: 29 Eylul 13:00 calismasi gaming slotunda Lethal
        # Company (1 klip), Roblox (1) ve Valorant (2) denedikten sonra burada
        # durdu ve 21:00 slotu bos kaldi — oysa havuzda 12 gaming konusundan
        # 11'i esigi geciyordu, 9 konu hic denenmedi. Artik havuz bitene kadar
        # deneniyor; timeout-minutes 330 bunun icin zaten genis birakilmisti.
    # Kaynak tam kadro veremedi. Bankada TEK BASINA tam kadroyu dolduran bir
    # konu varsa video ONDAN yapilir. Kullanici kurali 2026-08-15: banka videosu
    # tek konudan olacak ve tam 5 klip olacak; karisik ya da 3 kliplik video
    # istemiyor. Yarim kadro varsa banka hic dokunulmadan birakilir, o slot
    # gerekirse yedek kutuphaneden dolar.
    # Banka da yedek gibi gunun SON denemesine saklaniyor: 07:00 bloklu geldi
    # diye bankayi harcarsak, 10:00'daki yeni sunucunun gercek klip bulma sansi
    # bosa gider ve banka o gun kendini yenileyemez (kaynak bloklu).
    kaynak_klip = len(best[2]) if best else 0
    if kaynak_klip < CLIP_COUNT and son_deneme_mi():
        by_name = {t["topic"]: t for t in RANKING_TOPICS}
        # Banka SLOTUN GRUBUNDAN suzuluyor ve bugun cikmis konular atlaniyor.
        # Ikisi de 2026-09-05'te eklendi. Once yoktu ve iki ayri ariza uretti:
        # (1) gaming slotlari basarili olup fazlaliklarini bankaya yatiriyordu,
        # banka gaming agirlikli hale geliyordu, bankayi harcayan ise hep AC
        # KALAN (gaming disi) slot oluyordu — gunde uc gaming videosu; (2) ayni
        # konu hem taze hem bankadan cikip gunde iki kez yayinlanabiliyordu
        # (09-02'de GTA).
        bugunku = bugun_uretilenler(state)
        for aday in bank.topics_with_enough("ranking", CLIP_COUNT, group=grup):
            if aday in bugunku:
                print(f"  banka '{aday}' verebilirdi ama bugun zaten cikti, atlaniyor")
                continue
            cfg = by_name.get(aday) or bank.entry_for_topic("ranking", aday)
            if not cfg:
                continue
            b_items, b_links, b_metas = bank.take("ranking", aday, CLIP_COUNT)
            if not b_items:
                continue
            print(f"  kaynak {kaynak_klip} klip verdi — banka '{aday}' konusundan "
                  f"tam kadro ({len(b_items)}) verdi")
            used_list = list(state.get("ranking_topics_used", []))
            yeni_part = used_list.count(aday) + 1 if aday in used_list else None
            return (cfg, yeni_part, b_items, b_links, b_metas, "banka",
                    tried + [f"{aday} (banka)"])

    if not best:
        return None, None, [], [], [], "kaynak", tried
    topic_cfg, part, items, links, metas, source, _ = best

    # best, yakalandigi andaki kismi listeyi tasiyor; ozet e-postasinda
    # "denenen konular" dogru gorunsun diye tam listeyle degistiriyoruz.
    return topic_cfg, part, items, links, metas, source, tried


def find_satisfying_material(state, used_links):
    """Tum tatmin edici nislerden KARISIK klip toplar.

    (topic, variant, next_index, items, links, metas, source) doner. topic artik
    tek bir nis degil sabit "Satisfying Mix"; baslik da nis adi icermiyor, o
    yuzden karisim basligi yalanlamiyor. Alaka sinyali hashtag'in kendisi:
    #slimeasmr'dan gelen klip zaten o nise ait. Aciklamaya hashtag yigan
    alakasiz klipleri de quality.is_static yakaliyor."""
    n = int(state.get("satisfying_index") or 0)
    variant = SATISFYING_MIX_TITLES[n % len(SATISFYING_MIX_TITLES)]
    hashtags = satisfying_mix_hashtags()
    print(f"  karisim hashtag'leri: {hashtags}")
    topic = {"topic": "Satisfying Mix", "hashtags": hashtags,
             "required": MIX_REQUIRED, "queries": [], "titles": [variant]}
    try:
        items, links, metas, source = gather_clips(
            [], used_links, hashtags=hashtags, must_include=MIX_REQUIRED,
            clip_count=SATISFYING_CLIP_COUNT, min_digg=SATISFYING_MIN_DIGG,
            sesli_olsun=True, bastan_kes=True, tam_oynuyor=True,
            max_seconds=SATISFYING_MAX_SECONDS)
        # kind/topic VERILMIYOR: bu taramanin artiklari bankaya yatmasin.
        # Satisfying bankasi kullanilmiyor (yukaridaki aciklama), o yuzden
        # biriktirmek de gereksiz — bosuna indirme, bosuna depo.
    except discovery.SourceUnavailable as e:
        print(f"  kaynak erisilemiyor ({e})")
        items, links, metas, source = [], [], [], "kaynak"

    # Satisfying videosu BANKADAN kurulmuyor (kullanici bildirdi 2026-08-17:
    # "bankadan diye dunki videonun aynisi var"). Sebep yapisal: bankaya yatan
    # klipler, o gunku videonun taramasinda elemede kaybedenler. Yani banka
    # videosu, bir onceki videonun kardeslerinden olusuyor — ayni hesaplar, ayni
    # gorunum. Ranking'de konular farkli oldugu icin bu sorun yok, ama slime/ASMR
    # nisinde klipler zaten birbirine benziyor ve fark tamamen kayboluyor.
    # Kadro dolmazsa sira: IKINCI RANKING videosu (bkz. main), sonra yedek.
    return topic, variant, n + 1, items, links, metas, source


def adjectives_for(suffix):
    return ADJECTIVES_MOMENT if suffix == "Moments" else ADJECTIVES_FAIL


UPLOAD_LOG_CAP = 200


def record_upload(state, topic, title, result, question=""):
    """Yuklenen videoyu state'e yazar: hangi konu hangi videoya karsilik geldi.

    Su an sadece kayit — izlenme verisini okuyamadigimiz icin (token izni
    youtube.upload) kazanan konuyu otomatik secemiyoruz. Bu log, ileride bir
    analiz adimi eklendiginde "hangi konu tuttu" sorusunu cevaplayabilmek icin
    tutuluyor; o zamana kadar part_two_queue elle doldurulabilir."""
    url = (result or {}).get("url") or ""
    video_id = url.rsplit("/", 1)[-1] if url else ""
    log = state.setdefault("uploads", [])
    log.append({
        "date": (datetime.now(timezone.utc) + ISTANBUL_OFFSET).strftime("%Y-%m-%d"),
        "topic": topic,
        "title": title,
        "video_id": video_id,
        # Yorum BUGUN atilamaz: video su an gizli, 19:00/20:00'da yayinlanacak.
        # Soru burada bekletiliyor, bir sonraki calisma videoyu herkese acikken
        # bulup yorumu atiyor (bkz. yorumlari_gonder).
        "question": question or "",
    })
    state["uploads"] = log[-UPLOAD_LOG_CAP:]


MAX_YORUM_DENEMESI = 3


def video_kimligi(kayit):
    """state['uploads'] icindeki video_id alani iki bicimde olabiliyor:
    'eUhOs9zeaVQ' ya da 'watch?v=eUhOs9zeaVQ' (upload.py'nin bastigi URL'nin son
    parcasi aliniyor). Ikisini de tek bicime indirger."""
    ham = (kayit.get("video_id") or "").strip()
    if "v=" in ham:
        ham = ham.split("v=")[-1]
    return ham.split("&")[0]


def yorumlari_gonder(state, save=True):
    """DUNKU (artik yayinda olan) videolara sohbet baslatan yorumu atar.

    Neden dunku: gunluk akis videolari GIZLI yukluyor ve 19:00/20:00'a
    zamanliyor. Yukleme anindaki bir yorumun gorunecegi bir video yok. O yuzden
    soru state'e yaziliyor ve bir sonraki calisma, video herkese acikken atiyor.
    Yani bir video en gec ertesi sabah yorumunu almis oluyor.

    Yorum atmak gunun isini bozmamali: her hata yutuluyor, basarisiz kayit
    MAX_YORUM_DENEMESI kez tekrar deneniyor, sonra birakiliyor."""
    bugun_str = bugun()
    gonderilen = 0
    for kayit in state.get("uploads") or []:
        if kayit.get("commented") or not kayit.get("question"):
            continue
        if kayit.get("date") >= bugun_str:
            continue  # video henuz yayinlanmadi
        if kayit.get("comment_tries", 0) >= MAX_YORUM_DENEMESI:
            continue
        video_id = video_kimligi(kayit)
        if not video_id:
            continue
        if comment.yorum_yaz(video_id, kayit["question"]):
            kayit["commented"] = True
            gonderilen += 1
        else:
            kayit["comment_tries"] = kayit.get("comment_tries", 0) + 1
    if gonderilen and save:
        save_state(state)
    return gonderilen


def istanbul_today_at(hour, minute=0):
    now_ist = datetime.now(timezone.utc) + ISTANBUL_OFFSET
    target_ist = now_ist.replace(hour=hour, minute=minute, second=0, microsecond=0)
    target_utc = target_ist - ISTANBUL_OFFSET
    return target_utc.strftime("%Y-%m-%dT%H:%M:%SZ")


# Gunde DORT video (kullanici karari 2026-08-20): ikisi mevcut konseptten,
# ikisi gaming. 19:00/20:00 kanalin olculmus saatleri, o yuzden korundu ve
# genisletme onlerine/arkalarina yapildi.
DEFAULT_PUBLISH_TIMES = ["18:00", "19:00", "20:00", "21:00"]

# Hangi slot hangi icerik grubundan uretilir. None = gaming DISI (kanalin
# eski konseptleri), "gaming" = sadece gaming grubundaki konular.
#
# Neden gunluk bir "en fazla N" sinirI degil de slot bazli atama: sinir gaming
# secilmesini ENGELLER ama GARANTI ETMEZ. Gaming konularinin olculen verimi
# (Roblox 43, Fortnite 39, GTA 33, Minecraft 29) mevcut konularin cogundan
# yuksek oldugu icin en_verimlisi onlari one alirdi ve "2+2" bazi gunler 3+1
# olurdu. Slot bazli atama gunun dagilimini kesinlestiriyor.
#
# Gaming ve gaming-disi ARDISIK degil ALMASIK diziliyor: ayni izleyici akisina
# ust uste iki ayni tur video dusmesin.
SLOT_GRUPLARI = [None, "gaming", None, "gaming"]

# Bir gunde en fazla kac slot YEDEKTEN doldurulabilir (bkz. kullanildigi yer).
MAX_YEDEK_PER_GUN = 2


def slot_grubu(index):
    if 0 <= index < len(SLOT_GRUPLARI):
        return SLOT_GRUPLARI[index]
    return None


def bugun():
    return (datetime.now(timezone.utc) + ISTANBUL_OFFSET).strftime("%Y-%m-%d")


def dolu_slotlar(state):
    """Bugun ZATEN doldurulmus yayin saatleri.

    Neden gerekli: kaynak (tikwm) bazi GitHub sunucularini blokluyor, bazilarini
    degil — her calisma farkli bir sunucuya dustugu icin AYNI GUN IKINCI BIR
    DENEME yeni bir IP demek ve ise yarayabiliyor. Ama ikinci deneme guvenli
    olsun diye calismanin "bu saat zaten dolu mu" diye bakmasi sart, yoksa ayni
    saate ikinci video koyar. 2026-08-14'te gunun tek calismasi kaynaga
    erisemedi ve iki saat de yedekten dolduruldu.

    KAYIT DEGIL, KANAL esas alinir. State gercekle ayrisabiliyor: kullanici bir
    videoyu silince ya da yayin planini iptal edince kayit hala "dolu" diyor ve
    calisma o saati atliyor. 2026-08-17'de bu iki kez oldu; ikisinde de saat
    aslinda bostu ama calisma 1 dakikada "yapacak is yok" deyip cikti.
    YouTube'a bakilamazsa (ag/kota) kayda geri donuluyor."""
    kayit = state.get("filled_slots") or {}
    kayitli = set(kayit.get("slots") or []) if kayit.get("date") == bugun() else set()
    try:
        gercek = kanaldaki_dolu_saatler()
    except Exception as e:
        print(f"  kanal kontrol edilemedi ({e}) — slot kaydina guveniliyor")
        return kayitli
    if gercek != kayitli:
        print(f"  slot kaydi: {sorted(kayitli) or '-'} | kanalda gercekten dolu: "
              f"{sorted(gercek) or '-'} → kanal esas alindi")
    return gercek


def kanaldaki_dolu_saatler():
    """Bugun kanalda GERCEKTEN dolu olan yayin saatleri.

    Zamanlanmis video icin status.publishAt, yayinlanmis video icin
    snippet.publishedAt bakiliyor; ikisi de Istanbul saatine cevrilip
    DEFAULT_PUBLISH_TIMES ile eslestiriliyor."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    creds = Credentials.from_authorized_user_file(TOKEN_PATH)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    yt = build("youtube", "v3", credentials=creds)
    ch = yt.channels().list(part="contentDetails", mine=True).execute()["items"][0]
    pl = yt.playlistItems().list(
        part="contentDetails",
        playlistId=ch["contentDetails"]["relatedPlaylists"]["uploads"],
        maxResults=10).execute()
    ids = [i["contentDetails"]["videoId"] for i in pl["items"]]
    if not ids:
        return set()
    dolu = set()
    for v in yt.videos().list(part="snippet,status", id=",".join(ids)).execute()["items"]:
        zaman = v["status"].get("publishAt") or v["snippet"].get("publishedAt")
        if not zaman:
            continue
        try:
            ist = (datetime.strptime(zaman[:19], "%Y-%m-%dT%H:%M:%S")
                   .replace(tzinfo=timezone.utc) + ISTANBUL_OFFSET)
        except ValueError:
            continue
        if ist.strftime("%Y-%m-%d") != bugun():
            continue
        etiket = ist.strftime("%H:%M")
        if etiket in DEFAULT_PUBLISH_TIMES:
            dolu.add(etiket)
    return dolu


def slot_doldu(state, saat, save=True):
    kayit = state.get("filled_slots") or {}
    if kayit.get("date") != bugun():
        kayit = {"date": bugun(), "slots": []}
    if saat not in kayit["slots"]:
        kayit["slots"].append(saat)
    state["filled_slots"] = kayit
    if save:
        save_state(state)


def reserve_after_hour():
    """Yedek kutuphanesinin acildigi saat (Istanbul). 0 = hemen acik.

    KAPALI (0) OLMASI KASITLI VARSAYILAN. Bu erteleme SADECE gunun ikinci bir
    tetikleyicisi varsa mantikli; yoksa kaynagin bloklandigi bir gun tamamen bos
    gecer. 2026-08-14'te cron-job.org'da tek is vardi (09:00), o yuzden varsayilan
    kapali birakildi. Ikinci tetikleyici (13:00) kurulunca workflow'a
    'RESERVE_AFTER_HOUR: \"13\"' env satirini eklemek yeter."""
    try:
        return int(os.environ.get("RESERVE_AFTER_HOUR") or 0)
    except ValueError:
        return 0


def son_deneme_mi():
    """Bu calisma gunun SON denemesi mi? (Stoklar burada harcanir.)

    Hem klip bankasi hem yedek kutuphanesi bu kapiya bagli. Ikisi de sinirli
    stok; gunun ilk denemesinde harcanirlarsa, sonraki calismanin YENI bir
    sunucuda (yeni IP) gercek klip bulma sansi bosa gider.

    Kaynak (tikwm) bazi GitHub sunucularini bloklarken bazilarini bloklamiyor,
    o yuzden gunun IKINCI tetikleyicisi yeni bir IP ile GERCEK video uretme
    sansi demek. Sabahki calisma bos saatleri hemen yedekten doldurursa o sans
    yok oluyor ve elle stoklanan sinirli yedek bosa harcaniyor — 2026-08-14'te
    tam olarak bu oldu. Bu yuzden ikinci tetikleyici varken yedek gunun son
    denemesine birakiliyor (bkz. reserve_after_hour).

    FORCE_RESERVE=1 elle tetiklenen bir calismada saati beklemeden acar."""
    if os.environ.get("FORCE_RESERVE") == "1":
        return True
    return (datetime.now(timezone.utc) + ISTANBUL_OFFSET).hour >= reserve_after_hour()


def publish_slot(index):
    """(publishAt_utc, "HH:MM"). PUBLISH_TIMES env degiskeni ("21:00,21:30")
    o calismaya ozel saat vermek icin — workflow'un elle tetiklenen
    'publish_times' girdisinden geliyor, bos birakilirsa varsayilan."""
    raw = (os.environ.get("PUBLISH_TIMES") or "").strip()
    times = [t.strip() for t in raw.split(",") if t.strip()] if raw else []
    label = times[index] if index < len(times) else DEFAULT_PUBLISH_TIMES[index]
    try:
        hour, minute = (int(x) for x in label.split(":"))
    except Exception:
        label = DEFAULT_PUBLISH_TIMES[index]
        hour, minute = (int(x) for x in label.split(":"))
    return istanbul_today_at(hour, minute), label


# YouTube etiketleri ve aciklamadaki hashtag'ler. YouTube 15'ten fazla
# hashtag varsa hepsini yok sayiyor, o yuzden sinirli tutuluyor.
COMMON_TAGS = ["shorts", "viral", "fyp", "trending"]
RANKING_TAGS = ["fails", "fail compilation", "funny", "ranking", "top 5"]
SATISFYING_TAGS = ["asmr", "satisfying", "oddly satisfying", "relaxing", "satisfying video"]
MAX_TAGS = 15
MAX_HASHTAGS = 10
MAX_AI_TAGS = 4   # AI'dan gelen videoya-ozel hashtag sayisi (gerisi konu bazli)


def build_tags(topic, kind, suffix="Fails"):
    t = topic.lower()
    if kind == "ranking":
        s = suffix.lower()
        specific = [t, f"{t} {s}", f"funny {t}"] + RANKING_TAGS
    elif "satisfying" in t:
        # Konu adi zaten "satisfying" iceriyorsa sablon etiketler sacmaliyor:
        # "Satisfying Mix" -> "satisfying satisfying mix" (2026-08-16'da
        # yayinlanan videoda #satisfyingsatisfyingmix diye cikti). Bu durumda
        # konudan turetilen kalip etiket yerine dogrudan nis etiketleri.
        specific = ["satisfying videos", "asmr satisfying"] + SATISFYING_TAGS
    else:
        # Konu artik her gun slime degil, o yuzden etiketler de konudan uretiliyor.
        specific = [t, f"{t} asmr", f"satisfying {t}"] + SATISFYING_TAGS
    tags = []
    for tag in specific + COMMON_TAGS:
        if tag and tag not in tags:
            tags.append(tag)
    return tags[:MAX_TAGS]


def yedek_baslik(kayit):
    """Katalogdaki basliktan YAYIN basligi.

    Katalogda ayni ismi tasiyan videolar birbirinden ayrilsin diye sonlarina
    '(2 Agu)' gibi bir not konmustu; o not YouTube basligina cikmamali."""
    baslik = (kayit.get("title") or "").strip()
    return re.sub(r"\s*\([^)]*\)\s*$", "", baslik).strip() or baslik


def yedek_metadata(kayit):
    """Yedek videonun etiketleri + aciklamasi.

    Neden gerekli: yedekten yayinlanan videolar 2026-08-15'e kadar aciklama
    olarak SADECE basligi, etiket olarak da sadece 'shorts' aliyordu — normal
    uretimdeki konuya ozel hashtag'lerin hicbiri yoktu ve kullanici bunu
    farketti. Katalogdaki topic/kind/suffix alanlari (bkz. reserve.json) normal
    videolarla ayni build_tags/build_description yolunu kullanmayi sagliyor."""
    topic = kayit.get("topic") or kayit.get("title") or "shorts"
    kind = kayit.get("kind") or "ranking"
    suffix = kayit.get("suffix") or "Fails"
    tags = build_tags(topic, kind, suffix)
    intro = kayit.get("intro") or f"{yedek_baslik(kayit).rstrip('.')}."
    return tags, build_description(intro, tags, kind)


def hashtag_line(tags, limit=MAX_HASHTAGS):
    """Etiketleri YouTube hashtag'ine cevirir: bosluk/noktalama atilir
    ('camping fail' -> '#campingfail')."""
    hashtags = []
    for tag in tags:
        h = "#" + "".join(ch for ch in str(tag).lower() if ch.isalnum())
        if h != "#" and h not in hashtags:
            hashtags.append(h)
        if len(hashtags) >= limit:
            break
    return " ".join(hashtags)


# Abone cagrisi ACIKLAMADA duruyor, yorumda degil. Yorum sohbet baslatmali
# (bkz. yorumlari_gonder); "begendiyseniz abone olun" yorumu 13 aboneli bir
# kanalda dilenme gibi okunuyor ve cevap getirmiyor.
SUBSCRIBE_LINE = "Subscribe for a new ranking every day."
SUBSCRIBE_LINE_SATISFYING = "Subscribe for daily satisfying videos."


def build_description(intro, tags, kind="ranking"):
    line = SUBSCRIBE_LINE_SATISFYING if kind == "satisfying" else SUBSCRIBE_LINE
    return f"{intro}\n{line}\n\n{hashtag_line(tags)}"


# 100.000 -> 50.000 (kullanici karari 2026-08-13). Daha once 100k katiydi
# ("daha azini istemiyorum", 2026-08-02) ama dar hashtag'lerle aday havuzu
# yetmedigi ve videolar 3 klipte kaldigi icin esik dusuruldu.
MIN_DIGG_COUNT = 50000
# Once 5'ten 6'ya cikarildi (milyonlarca izlenen ayni format videolari 6-7 madde
# listeliyor), sonra kullanici karariyla 5'e geri dondu (2026-08-09): 6 klip
# hem render suresini ~%20 uzatiyor hem videoyu 1 dakikaya yaklastiriyordu.
# 5 klip x 10sn = 50sn, 1 dakikanin altinda kaliyor.
CLIP_COUNT = 5

# Bir video icin gereken en az klip sayisi = TAM KADRO. Eskiden 3'tu ve
# kullanici cikan ince videolari iki kez bildirdi ("3 klipli istemiyoruz").
# Kadro dolmuyorsa video uretilmiyor; o saat once BANKADAN (tek konu, tam
# kadro), o da yoksa YEDEK kutuphaneden doluyor. Yani "3 klip mi, hic mi"
# ikilemi yok — eksik kadro yerine bankadaki tam kadro cikiyor.
MIN_CLIPS_FOR_VIDEO = CLIP_COUNT

MAX_INDIRME_DENEMESI = 18
MIN_CLIP_SECONDS = 3
MAX_CLIP_SECONDS = 15     # render.render'daki max_clip_seconds ile ayni
# Begeni sarti KATI (MIN_DIGG_COUNT). Sure ise tercih: kisa klip tam oynar,
# uzun klip kirpilir. Onceki surumde kati katmanlar vardi ve 120k begenili
# kisa bir klip, 2.2M begenili 24sn'lik klibin onune geciyordu. Kullanici
# 2026-08-02'de "yine de en cok like olanlardan secmeye calis" dedi, o yuzden
# artik begeni sayisi sure agirligiyla carpiliyor: uzun klip cezalandiriliyor
# ama cok daha populerse yine one gecebiliyor.
# Aday klip icin SERT sure tavani. Once sadece agirlik vardi (uzun klip dusuk
# puan alirdi ama yine secilebilirdi) ve havuz daralinca uzunlar videoya
# giriyordu: 2026-08-13'te bankaya 188 saniyelik bir slime klibi girdi, klip
# basina sinir 10 saniye oldugu icin 3 dakikalik videodan rastgele 10 saniye
# kesiliyordu ve ne oldugu anlasilmiyordu (kullanici bildirdi). Kural
# 2026-08-02'den beri ayni: KISA klip tam oynar, uzun klip kirpilir ve
# kirpilan klipler "konusma yarida kesilmis / kopuk" sikayetlerinin kaynagi.
# 25sn tavani, 10sn'lik klip siniriyla en fazla 2.5 kat kirpma demek.
# Tavani elle degil ORANDAN turetiyoruz: klip basina verilen sureyle aday
# tavani arasindaki oran sabit kalsin ki hangi nis olursa olsun kirpma miktari
# ayni olsun. 2.5x = 10sn oynatilacak klip icin en fazla 25sn'lik aday.
TRIM_RATIO = 2.5


def max_candidate_seconds(clip_count):
    return int(clip_cap(clip_count) * TRIM_RATIO)


# Tam oynayan klip en yuksek puani alir; kirpma arttikca puan duser.
DURATION_WEIGHTS = [(10, 1.0), (15, 0.7), (25, 0.4), (40, 0.25)]


# Susturulacak klibin secim puani bu carpanla dusuruluyor. Neden: telif riski
# olan klipler (music_info.original == False) render'da volume=0 ile
# susturuluyor ve o klip boyunca video TAM SESSIZ kaliyor — 5 kliplik bir
# videoda 2 susturulmus klip 20 saniye sessizlik demek, hepsi susturulmussa
# video bastan sona sessiz. Kullanici 2026-08-11'de "bazi videolarda ses yok"
# diye bildirdi. Olculen oran: uygun kliplerin ~%18'i susturuluyor. Silmek
# yerine cezalandiriyoruz: sesli klip varsa o secilir, yoksa susturulmus klip
# yine de kullanilabilir (video atlanmasin diye).
MUTED_PENALTY = 0.2


def selection_score(video, muted=False):
    duration = video.get("duration") or 0
    penalty = MUTED_PENALTY if muted else 1.0
    for limit, weight in DURATION_WEIGHTS:
        if duration <= limit:
            return (video.get("digg_count") or 0) * weight * penalty
    return 0


def gather_clips(queries, used_links, must_include=None, hashtags=None,
                 clip_count=None, kind=None, topic=None, title_topic=None,
                 suffix=None, min_digg=None, sesli_olsun=False, bastan_kes=False,
                 max_seconds=None, tam_oynuyor=False):
    """Kaynaktan klip toplar. (items, links, metas, kaynak_notu) doner.

    kind/topic verilirse videonun ihtiyacinin BIR KAT fazlasi klip cekilir ve
    artan tam kadro klip bankasina yatirilir (bkz. bank.topup_for) — kaynagin
    bloklandigi bir gunde AYNI KONUDAN gercek bir video cikabilsin diye.
    Fazlalik, videoya girenler secildikten SONRA arta kalanlardir; yani banka
    o gunku videonun kalitesini dusurmez, elemede kaybedenleri saklar.
    """
    clip_count = clip_count or CLIP_COUNT
    bankaya = bool(kind and topic)
    topup = bank.topup_for(kind, clip_count, topic) if bankaya else 0
    items, links, metas = gather_items(
        queries, count=clip_count + topup, used_links=used_links,
        must_include=must_include, hashtags=hashtags,
        max_seconds=max_seconds or max_candidate_seconds(clip_count),
        min_digg=min_digg, sesli_olsun=sesli_olsun, bastan_kes=bastan_kes,
        tam_oynuyor=tam_oynuyor)

    # Videoya hangi klipler girecek: begeni x SURE AGIRLIGI x susturma cezasi.
    order = sorted(
        range(len(items)),
        key=lambda k: selection_score(metas[k], muted=bool(items[k].get("mute"))),
        reverse=True)
    items = [items[k] for k in order]
    links = [links[k] for k in order]
    metas = [metas[k] for k in order]

    artan = list(zip(items[clip_count:], links[clip_count:], metas[clip_count:]))
    items, links, metas = items[:clip_count], links[:clip_count], metas[:clip_count]

    if bankaya and artan:
        yatan = 0
        for it, ln, mt in artan:
            # Yorumlar SIMDI cekiliyor: etiketleri Gemini bunlardan uretiyor ve
            # banka klibi kullanilacagi gun kaynak muhtemelen bloklu olacak.
            try:
                time.sleep(discovery.RATE_LIMIT_SLEEP)
                yorumlar = discovery.top_comments(mt.get("video_id"))
            except Exception:
                yorumlar = []
            if bank.add(kind, it, mt, ln, topic, title_topic, suffix, yorumlar,
                        group=konu_grubu(topic)):
                yatan += 1
        if yatan:
            print(f"  bankaya {yatan} klip yatirildi "
                  f"('{topic}' toplam {bank.counts_by_topic(kind).get(topic, 0)})")

    # 1. sira en cok begenilen olsun.
    order = sorted(range(len(items)),
                   key=lambda k: (metas[k].get("digg_count") or 0), reverse=True)
    return ([items[k] for k in order], [links[k] for k in order],
            [metas[k] for k in order], "kaynak")


def gather_items(queries, count=5, used_links=None, must_include=None, hashtags=None,
                 max_seconds=None, min_digg=None, sesli_olsun=False,
                 bastan_kes=False, tam_oynuyor=False):
    """Aday havuzunu tikwm aramasindan kurar, filtreler, indirir ve kalite
    kontrolunden gecenleri begeni sirasina gore dondurur.

    Filtreler: >= min_digg begeni (kati), daha once kullanilmamis, tepki
    videosu degil, (varsa) konu kelimesi basligda geciyor; indirdikten sonra da
    sessiz ve derleme klipler eleniyor."""
    used_links = used_links or set()
    min_digg = MIN_DIGG_COUNT if min_digg is None else min_digg
    videos = discovery.find_videos(hashtags=hashtags, queries=queries)
    print(f"  {len(videos)} benzersiz sonuc tarandi")

    pool = []
    for v in videos:
        link = discovery.video_link(v)
        if link in used_links:
            continue
        if (v.get("digg_count") or 0) < min_digg:
            continue
        title = v.get("title") or ""
        if is_reaction_title(title):
            continue
        if uygunsuz_baslik(title):
            print(f"  atlandi (uygunsuz aciklama): {title[:60]}")
            continue
        if must_include and not any(w in title.lower() for w in must_include):
            continue
        sure = v.get("duration") or 0
        if sure < MIN_CLIP_SECONDS:
            continue
        if max_seconds and sure > max_seconds:
            continue
        # sesli_olsun: telif riski yuzunden SUSTURULACAK klipleri hic alma.
        # Satisfying/ASMR videosunda ses icerigin kendisi; susturulmus bir klip
        # 10 saniye sessizlik demek. Kullanici 2026-08-16'da tekrar bildirdi
        # ("yine bazi yerlerde ses yok"), Ranking tarafinda ise susturma hala
        # sadece puan cezasi — orada goruntu tek basina isi goruyor.
        if sesli_olsun and not discovery.is_original_sound(v):
            continue
        pool.append((link, v))
    print(f"  {len(pool)} aday {min_digg}+ begeni ve filtreleri gecti")

    # Begeni x sure-agirligi: populerlik onde, kisalik tercih.
    ordered = sorted(
        pool,
        key=lambda p: selection_score(p[1], muted=not discovery.is_original_sound(p[1])),
        reverse=True)

    items = []
    accepted = []
    used_this_run = []
    tmp_dir = tempfile.mkdtemp(prefix="rankmaker_daily_")
    for attempt, (link, v) in enumerate(ordered):
        # Indirme tavani. Eskiden "count * 4" idi ve count = klip + banka
        # yatirimi (10'a kadar) oldugu icin TEK bir konu denemesi 40 indirme +
        # 40 ffmpeg + 80 OpenCV gecisi yapabiliyordu. Sinirsiz konu denemesiyle
        # birlikte bu CI suresini patlatirdi.
        if len(items) >= count or attempt >= MAX_INDIRME_DENEMESI:
            break
        path = os.path.join(tmp_dir, f"clip{attempt}.mp4")
        try:
            tiktok.download_from_info(discovery.download_info(v), path)
        except Exception as e:
            print(f"  indirilemedi ({link}): {e}")
            continue
        if not quality.has_audio(path):
            print(f"  atlandi (sessiz): {link}")
            continue
        # KLIP BASINA TEK ANALIZ. Eskiden looks_like_compilation ve is_static
        # ayri ayri motion.analyze_motion cagiriyordu, yani her klip en az IKI
        # kez bastan sona (0.3sn adimlarla) cozuluyordu. Bir kez cozup ikisine
        # de veriyoruz; hem CI suresi dusuyor hem de asagidaki pencere hesabi
        # icin ornekler zaten elimizde oluyor.
        try:
            sure, ornekler = motion.analyze_motion(path)
        except Exception as e:
            print(f"  hareket analizi yapilamadi ({link}): {e}")
            sure, ornekler = 0, None

        # Derleme filtresi, UZUN klibi kirpinca ortaya cikan kopukluk icin var.
        # Klip tam oynuyorsa (tam_oynuyor) izleyicinin gordugu sey creator'in
        # kendi videosu; 7 saniyelik bir klipteki ic kesme derleme degil.
        # Kullanici karari 2026-08-16: satisfying tarafinda bu filtre kapali,
        # cunku adaylarin ucte biri buna takilip kadro dolmuyordu.
        #
        # 2026-09-29: ranking tarafinda filtre KLIBIN TAMAMINA bakiyordu, oysa
        # videoya sadece clip_cap(CLIP_COUNT) kadari giriyor (aday tavani 25sn,
        # giren ~10sn). #174 calismasinda begeni barini gecen 83 adaydan sadece
        # 4'u bu filtreden gecti ve 21:00 slotu bos kaldi. Artik SADECE
        # gosterilecek pencere taraniyor — render'in kullanacagi pencerenin
        # ayni hesapla bulunmus hali (motion.best_motion_window, ki o zaten
        # kesmelerin ARASINDAKI segmentten secim yapiyor). Esik degismedi.
        pencere = None
        if ornekler and not tam_oynuyor:
            kapak = clip_cap(CLIP_COUNT)
            if bastan_kes:
                pencere = (0.0, kapak)
            elif sure:
                secim = motion.best_motion_window(ornekler, sure, min(kapak, sure))
                pencere = (secim["start"], secim["length"])
        if not tam_oynuyor and quality.looks_like_compilation(
                path, pencere=pencere, samples=ornekler):
            print(f"  atlandi (derleme): {link}")
            continue
        if quality.is_static(path, samples=ornekler):
            print(f"  atlandi (hareketsiz/ekran goruntusu): {link}")
            continue
        mute = not discovery.is_original_sound(v)
        if mute:
            print(f"  klip sesi susturulacak (bilinen/lisansli ses): {link}")
        items.append({"path": path, "label": "", "emoji": "", "mute": mute,
                      "from_start": bastan_kes})
        accepted.append(v)
        used_this_run.append(link)
        print(f"  secildi ({v.get('digg_count')} begeni, {v.get('duration')}sn): {link}")

    # 1. sira en cok begenilen olsun (sure katmanlari siralamayi bozmustu).
    order = sorted(range(len(items)), key=lambda k: accepted[k].get("digg_count", 0), reverse=True)
    items = [items[k] for k in order]
    accepted = [accepted[k] for k in order]
    used_this_run = [used_this_run[k] for k in order]
    return items, used_this_run, accepted


def apply_metadata(items, accepted, video_title, fallback_tags,
                   zorunlu_etiket=True):
    """Klip basliklari + en begenilen yorumlardan AI ile hem kisa etiket hem
    de videoya OZEL hashtag uretir. Hashtag'ler AI'dan gelmezse jenerik
    fallback listesi kullanilir (kullanici istegi 2026-08-02: her videoda
    ayni hashtag'ler olmasin, gercekten gorunurluk saglasin)."""
    meta = []
    for v in accepted:
        # Banka klipleri yorumlarini yanlarinda getiriyor: o gun kaynak bloklu
        # oldugu icin yeniden cekmek hem imkansiz hem gereksiz.
        if v.get("comments"):
            meta.append({"title": v.get("title") or "", "comments": list(v["comments"])})
            continue
        time.sleep(discovery.RATE_LIMIT_SLEEP)
        meta.append({
            "title": v.get("title") or "",
            "comments": discovery.top_comments(v.get("video_id")),
        })
    data = labeler.generate_metadata(meta, video_title)

    labels = data.get("labels") or []
    for item, label in zip(items, labels):
        item["label"] = label
    print("  etiketler: " + ", ".join(repr(l) for l in labels))
    if zorunlu_etiket and not any(l for l in labels):
        # Etiketsiz video YAYINLANMAZ. 2026-08-16'da bu Mac'te GEMINI_API_KEY
        # olmadigi icin butun etiketler bos geldi ve uzerinde tek yazi olmayan
        # bir video YouTube'a planlandi. Etiketler bu formatin ta kendisi;
        # etiketsizse video eksik demektir. Anahtar sadece GitHub secret'inda,
        # yani yerel elle calismalar bu kapiya takilir - dogrusu da bu.
        raise RuntimeError(
            "etiket uretilemedi (GEMINI_API_KEY yok ya da servis cevap vermedi) "
            "- etiketsiz video yayinlanmaz")

    # Eskiden hashtag'lerin TAMAMI AI'dan geliyordu ve icerikle ilgisi olmayan
    # seyler kaciyordu (#aikitten, #mckennagrace, #anklemonitor, #doubledenim —
    # muhtemelen klip aciklamalarindaki sakalardan turetilmis). Alakasiz
    # metadata YouTube icin kotu bir sinyal, o yuzden artik konu bazli
    # cekirdek etiketler garanti, AI'dan gelenler en fazla MAX_AI_TAGS tane
    # eklenti olarak kullaniliyor.
    ai_tags = [t for t in (data.get("hashtags") or []) if t]
    tags = list(fallback_tags)
    added = 0
    for t in ai_tags:
        if added >= MAX_AI_TAGS:
            break
        if t not in tags:
            tags.append(t)
            added += 1
    print(f"  AI hashtag'lerinden {added} tanesi cekirdek listeye eklendi")
    if "shorts" not in tags:
        tags.append("shorts")
    tags = tags[:MAX_TAGS]
    print("  hashtagler: " + hashtag_line(tags))
    soru = data.get("question") or ""
    if soru:
        print(f"  yorum sorusu: {soru}")
    return tags, soru


def out_dir():
    return os.environ.get("RANKMAKER_OUT_DIR") or tempfile.gettempdir()


def write_summary(summary):
    """E-posta bildirimi (pipeline/notify.py) bu dosyayi okuyor. Her videodan
    sonra yeniden yaziliyor ki calisma yarida kalsa bile kismi ozet kalsin."""
    try:
        path = os.path.join(out_dir(), SUMMARY_NAME)
        os.makedirs(out_dir(), exist_ok=True)
        with open(path, "w") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"  ozet yazilamadi: {e}")


# Toplam video suresi hedefi. 2-8 Agustos arasi uretilen her video 1:02-1:15
# arasindaydi (5 klip x 15sn) ve hepsi 1-5 goruntulemede kaldi. 60 saniyenin
# altinda kalmak iki sey kazandiriyor: Shorts'ta tamamlanma orani (dagitimin
# ana sinyali) yukseliyor, ve her klipten daha az saniye ses kullanildigi icin
# Content ID'nin eslesme esigine takilma sansi dusuyor.
#
# NOT: 2026-08-09'da videonun basina 2 saniyelik bir "aksiyon kancasi" eklendi,
# sonra KALDIRILDI. Sebep: milyonlarca izlenen ayni format kanallari
# (@whorankedit, "Ranking Worst ASMR Fails" videolari) bunu yapmiyor — ilk kare
# dogrudan baslik + liste. Kullanici karari, tekrar eklemeyin.
TARGET_TOTAL_SECONDS = 50
MIN_CLIP_CAP_SECONDS = 8


def clip_cap(clip_count):
    """Klip basina saniye siniri: toplam TARGET_TOTAL_SECONDS'i gecmesin,
    ama MAX_CLIP_SECONDS'i de asmasin (3 klip kalirsa gereksiz uzatmayalim)."""
    per_clip = int(TARGET_TOTAL_SECONDS // max(1, clip_count))
    return min(MAX_CLIP_SECONDS, max(MIN_CLIP_CAP_SECONDS, per_clip))


def render_and_upload(items, title_cfg, out_name, publish_at, description, tags,
                      upload=True, cap=None, youtube_title=None):
    target_dir = out_dir()
    os.makedirs(target_dir, exist_ok=True)
    out_path = os.path.join(target_dir, out_name)
    # cap verilmezse toplam sureyi klip sayisina bolen varsayilan. Satisfying
    # tarafi kendi ust sinirini veriyor (SATISFYING_MAX_SECONDS) cunku oradaki
    # klipler zaten o sureden kisa secildi ve TAM oynamalari gerekiyor.
    cap = cap or clip_cap(len(items))
    print(f"  {len(items)} klip, klip basina en fazla {cap}sn "
          f"(hedef toplam <= {TARGET_TOTAL_SECONDS}sn)")
    render.render(items, title_cfg, out_path, max_clip_seconds=cap, fps=30)
    print(f"  render edildi: {out_path}")
    # Bankadan cekilen klipler manifest'ten dusuruldu; dosyalari da simdi silinir
    # (render bitti, artik okunmuyorlar). Bir banka klibi sadece bir kez kullanilir.
    silinen = bank.cleanup_consumed()
    if silinen:
        print(f"  bankadan tuketilen {silinen} klip dosyasi silindi")

    # Ekran basligi (title_cfg) konuyu anlatiyor; YouTube baslik alanina
    # youtube_title verilmisse kanca cumlesi gidiyor (bkz. youtube_basligi).
    ekran_basligi = f"{title_cfg['prefix']} {title_cfg['adjective']} {title_cfg['topic']} {title_cfg['suffix']}".strip()
    full_title = youtube_title or ekran_basligi
    if not upload:
        print("  (yukleme atlandi: --no-upload)")
        return {"title": full_title, "status": "rendered", "url": None}

    cmd = [
        sys.executable, UPLOAD_SCRIPT, out_path,
        "--title", full_title,
        "--description", description,
        "--tags", ",".join(tags),
        "--privacy", "private",
        "--publish-at", publish_at,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        raise RuntimeError(f"Yukleme basarisiz: {out_name}")

    url = None
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                url = json.loads(line).get("url")
            except Exception:
                pass
    return {"title": full_title, "status": "uploaded", "url": url}


def summarize_clips(items, metas):
    return [
        {"label": it.get("label") or "", "likes": m.get("digg_count"), "duration": m.get("duration")}
        for it, m in zip(items, metas)
    ]


def ranking_videosu_uret(state, used_links, summary, slot, upload, save,
                         topic_override=None, etiket="Ranking", grup=None):
    """Bir Ranking videosu uretir ve verilen yayin saatine planlar.

    Uretildiyse True doner. Ayri fonksiyon olmasinin sebebi: satisfying
    videosu icin yeterli klip bulunamadigi gunlerde o saati YEDEKTEN
    doldurmak yerine IKINCI BIR RANKING videosu uretiyoruz (kullanici karari
    2026-08-16). Ranking havuzu satisfying havuzundan cok daha genis, yani
    gunun ikinci videosu taze uretim olabiliyor ve yedek harcanmiyor.
    """
    # --- Ranking videosu ---
    print(f"=== {etiket} ===")
    (topic_cfg, part, ranking_items, used1, ranking_meta,
     ranking_source, tried_topics) = find_ranking_material(
        state, used_links, forced_topic=topic_override, grup=grup)
    # Baslikta konu adi degil title_topic kullaniliyor. Neden: hashtag'ler
    # konu adindan daha genis olabiliyor ve baslik o zaman klipte olmayan
    # bir sey vaat ediyor. 2026-08-13'te kullanici bildirdi: "Raccoon
    # Stealing Moments" basligiyla cikan videodaki kliplerin hepsi calma
    # degildi, cunku hashtag'ler #raccoon/#trashpanda idi. title_topic,
    # hashtag'lerin GARANTI ettigi ortak seydir.
    topic = topic_cfg["topic"] if topic_cfg else "-"
    title_topic = (topic_cfg.get("title_topic") or topic) if topic_cfg else "-"
    suffix = topic_cfg["suffix"] if topic_cfg else "Fails"
    # Grup gecisi olduysa ozete yazilir: slotun grubu ile secilen konunun grubu
    # tutmuyorsa (bkz. find_ranking_material) o gun 2 yerine 1 gaming videosu
    # cikmis demektir ve bunun bildirim e-postasinda gorunmesi gerekiyor.
    if topic_cfg and (topic_cfg.get("group") or None) != (grup or None):
        summary["group_fallback"] = summary.get("group_fallback", 0) + 1
        print(f"  NOT: bu slot {grup or 'gaming disi'} grubundaydi, "
              f"{topic_cfg.get('group') or 'gaming disi'} konuyla dolduruldu")
    adjective = random.choice(adjectives_for(suffix))
    part_label = f" Part {part}" if part else ""
    if topic_cfg:
        print(f"=== Ranking: {adjective} {title_topic} {suffix}{part_label} "
              f"({len(tried_topics)} konu denendi) ===")
    if len(ranking_items) < MIN_CLIPS_FOR_VIDEO:
        print(f"Yeterli klip bulunamadi (Ranking/{topic}), bu video atlaniyor.")
        summary["videos"].append({
            "kind": etiket, "status": "skipped",
            "reason": f"yeterli klip bulunamadi (denenen konular: "
                      f"{', '.join(tried_topics) or '-'})",
        })
        write_summary(summary)
    else:
        ranking_cfg = {
            "prefix": "Ranking", "prefixColor": "#ffffff",
            "adjective": adjective, "adjectiveColor": "#ff3b30",
            "topic": title_topic, "topicColor": "#ffd400",
            # "Part 2" EKRANDA yazmiyor (kullanici karari 2026-08-17). YouTube
            # basliginda duruyor — orada devam videosu oldugunu gostermek ise
            # yariyor — ama video icindeki baslik kartinda gereksiz yer kapliyor
            # ve izleyiciye "bunu kacirdim" hissi veriyor.
            "suffix": suffix, "suffixColor": "#ffffff",
            "hookLine": random.choice(HOOK_LINES),
        }
        # "Part 2" ne ekranda ne YouTube basliginda yaziyor (kullanici karari
        # 2026-08-17). Devam videosu oldugu bilgisi state'te duruyor
        # (part_two_queue / uploads), izleyiciye gosterilmiyor.
        ranking_title = f"Ranking {adjective} {title_topic} {suffix}"
        publish_at_1900, publish_label_1 = publish_slot(slot)
        try:
            ranking_tags, ranking_soru = apply_metadata(
                ranking_items, ranking_meta, ranking_title,
                build_tags(title_topic, "ranking", suffix),
                zorunlu_etiket=upload)
        except RuntimeError as e:
            print(f"{etiket} videosu atlaniyor: {e}", file=sys.stderr)
            summary["videos"].append({
                "kind": etiket, "status": "skipped", "reason": str(e)})
            write_summary(summary)
            ranking_tags = None
        if ranking_tags is not None:
            result = render_and_upload(
                ranking_items, ranking_cfg, "ranking.mp4", publish_at_1900,
                youtube_title=youtube_basligi(ranking_title, state),
                description=build_description(
                    f"Ranking the {adjective.lower()} {title_topic.lower()} "
                    f"{suffix.lower()}.", ranking_tags),
                tags=ranking_tags,
                upload=upload,
            )
            summary["videos"].append({
                "kind": etiket, "clip_source": ranking_source,
                "publish_at": publish_at_1900,
                "publish_local": publish_label_1,
                "clips": summarize_clips(ranking_items, ranking_meta),
                **result,
            })
            write_summary(summary)
            used_links.update(used1)
            if save:
                state.setdefault("ranking_topics_used", []).append(topic)
                # Bu konu Part kuyrugundan geldiyse kuyruktan dus.
                queue = state.get("part_two_queue") or []
                if queue and queue[0] == topic:
                    state["part_two_queue"] = queue[1:]
                record_upload(state, topic, ranking_title, result, ranking_soru)
                # Slot DOLDU. 2026-08-16'da bu satir yoktu: slot kaydi sadece
                # yedek yayinlarken tutuluyordu, o yuzden 07:00 calismasi
                # 19:00'a video koyduktan sonra 10:00 calismasi "19:00 bos"
                # sanip AYNI SAATE ikinci videoyu koydu (Ice Skating + Zoomies).
                if result.get("status") == "uploaded":
                    slot_doldu(state, publish_label_1, save=False)
                save_state(state)
                save_used_clips(used_links)
            return True

    return False

def main(upload=True, save=True, only=None, topic_override=None):
    # --only: tek bir slotu tamamlamak icin. Eskiden "ranking"/"satisfying"
    # idi; dort slota gecince slot NUMARASI da kabul ediliyor ("0".."3").
    # Eski adlar calismaya devam ediyor ki elle tetiklenen workflow girdisi
    # ve alisilmis kullanim kirilmasin.
    hedef_slot = None
    if only is not None:
        eski_adlar = {"ranking": 0, "satisfying": 1}
        if only in eski_adlar:
            hedef_slot = eski_adlar[only]
        else:
            try:
                hedef_slot = int(only)
            except (TypeError, ValueError):
                hedef_slot = None
        if hedef_slot is None or not (0 <= hedef_slot < len(DEFAULT_PUBLISH_TIMES)):
            print(f"UYARI: --only '{only}' anlasilmadi, tum slotlar uretilecek",
                  file=sys.stderr)
            hedef_slot = None
        else:
            print(f"SADECE {publish_slot(hedef_slot)[1]} slotu uretilecek (--only {only})")

    state = load_state()
    used_links = load_used_clips()

    # Manifest ile disk ayrisabiliyor (2026-08-13'te yerel bir testten commit'lenen
    # manifest CI'da olmayan dosyalari gosterdi ve banka dolu sanildi).
    # Konu skorlari bayatsa burada tazeleniyor — boylece tarama da bulutta
    # calisiyor ve kullanicinin bilgisayarina ihtiyac kalmiyor. Uc gunde bir,
    # yani calismalarin cogu bu maliyeti odemiyor.
    if upload:
        from pipeline import topic_scan
        topic_scan.gerekiyorsa_tara(RANKING_TOPICS)

    dusen = bank.prune_missing()
    if dusen:
        print(f"bankada {dusen} kayit dosyasiz cikti, manifest temizlendi")
    print(f"banka (ranking): {bank.counts_by_topic('ranking') or 'bos'}")

    # Slot kilidi SADECE gercek calismalarda. Test calismasi (--no-upload)
    # YouTube'a hicbir sey koymadigi icin cifte video riski yok; kilidi orada da
    # uygulamak "kaynak su an calisiyor mu" sorusunu test etmeyi imkansiz
    # kiliyordu — 2026-08-15'te saatler yedekle dolu oldugu icin test calismasi
    # (run #29) uretime hic girmeden 26 saniyede bitti ve hicbir sey olcemedi.
    zaten_dolu = dolu_slotlar(state) if upload else set()
    if zaten_dolu:
        print(f"bugun zaten dolu olan saatler: {sorted(zaten_dolu)} — atlanacak")
    uretilecek = [i for i in range(len(DEFAULT_PUBLISH_TIMES))
                  if publish_slot(i)[1] not in zaten_dolu
                  and (hedef_slot is None or i == hedef_slot)]

    # Gunun BUTUN saatleri zaten doluysa bu calismanin yapacagi is yok ve bu bir
    # HATA DEGIL. Gunde iki tetikleyici oldugu icin (09:00 bloklanirsa 13:00 yeni
    # bir sunucu/IP dener) sabahki calisma basarili gectiginde ogleki her gun bu
    # duruma dusuyor; "sifir uretim = exit 1" kurali burada yanlis alarm olurdu.
    yapacak_is_yok = all(publish_slot(i)[1] in zaten_dolu
                         for i in range(len(DEFAULT_PUBLISH_TIMES)))
    if yapacak_is_yok:
        print("bugunun tum yayin saatleri dolu — bu calismanin yapacagi is yok")

    summary = {
        "date": (datetime.now(timezone.utc) + ISTANBUL_OFFSET).strftime("%Y-%m-%d"),
        "dry_run": not upload,
        "already_filled": sorted(zaten_dolu),
        "nothing_to_do": yapacak_is_yok,
        "videos": [],
    }
    write_summary(summary)

    # GUNDE DORT VIDEO (kullanici karari 2026-08-20): 2 mevcut konsept +
    # 2 gaming, dagilim SLOT_GRUPLARI ile sabit. Eskiden iki sabit cagri
    # vardi (ranking + "ikinci ranking"); slot sayisi degistiginde koda
    # dokunmak gerekmesin diye donguye cevrildi.
    #
    # Satisfying formati 2026-08-17'de kaldirildi ve geri gelmedi; kodu
    # (find_satisfying_material, MIX_*) duruyor ama gunluk akista yok.
    # Gerekce olculmustu: satisfying havuzu kadroyu dolduramiyordu ve en iyi
    # satisfying videosu 972 izlenmede kalirken futbol 20.516 aldi.
    for slot in uretilecek:
        saat = publish_slot(slot)[1]
        # Her uretimden ONCE kanala tekrar bakiliyor: ayni calismada onceki
        # slotlar doldu, ayrica baska bir tetikleyici arada video koymus
        # olabilir. 2026-08-16'da bu kontrol olmadigi icin ayni saate iki
        # video bindi.
        if saat in dolu_slotlar(state):
            print(f"{saat} bu arada doldu — atlaniyor")
            continue
        grup = slot_grubu(slot)
        etiket = f"Ranking {saat}" + (" [gaming]" if grup else "")
        print(f"=== {etiket} ===")
        ranking_videosu_uret(
            state, used_links, summary, slot, upload, save,
            topic_override=topic_override if slot == uretilecek[0] else None,
            etiket=etiket, grup=grup)

    # Gunun EKSIK KALAN her videosu icin yedek kutuphanesinden bir video
    # yayinla. Kullanici karari 2026-08-13/14: "bir gun video yuklenemezse eski
    # videolardan birini yukle, en cok tutandan basla" ve "iki video da
    # yuklenmediyse ikisini de yedekten al, 19:00 ve 20:00'a".
    # Ilk surum sadece TEK video yayinliyordu ve hep 19:00'a koyuyordu; gunun
    # ikinci saati bos kaliyordu.
    # Yedekler elle indirilip pipeline/reserve/ icine konuyor (bkz. reserve.py —
    # YouTube API'de kendi videonu indirme uc noktasi yok).
    #
    # AMA yedek gunun SON denemesine birakiliyor (bkz. son_deneme_mi). Neden:
    # 2026-08-14'te sabahki calisma kaynaga erisemedi ve iki saati de aninda
    # yedekten doldurdu; gunun ikinci tetikleyicisi (13:00, YENI bir sunucu =
    # yeni bir IP = kaynak muhtemelen acik) o yuzden "saatler dolu" deyip hicbir
    # sey yapamazdi. Yedek kutuphanesi sinirli (elle stokllaniyor), gercek video
    # her zaman yedege tercih edilir.
    if upload:
        dolu_saatler = {v.get("publish_local") for v in summary["videos"]
                        if v.get("status") in ("uploaded", "rendered")}
        dolu_saatler |= dolu_slotlar(state)
        bos_slotlar = [s for s in range(len(DEFAULT_PUBLISH_TIMES))
                       if publish_slot(s)[1] not in dolu_saatler]
        # YEDEK SADECE KAYNAK BLOKLUYSA. Kullanici karari 2026-09-05: havuz
        # zayif diye eski format bir re-upload yayinlanmasin, o slot bos kalsin.
        # Yedek yine de duruyor cunku kaynak komple bloklandiginda taranacak
        # hicbir sey yok — o gun tek secenek yedek.
        summary["source_blocked"] = KAYNAK_BLOKLU
        if bos_slotlar and not KAYNAK_BLOKLU:
            print(f"{len(bos_slotlar)} slot bos ama kaynak bloklu DEGIL — "
                  "yedek acilmiyor, slot bos birakiliyor "
                  "(havuz zayifligi yedekle kapatilmaz)")
            summary["reserve_skipped_pool_thin"] = len(bos_slotlar)
            bos_slotlar = []
        # --only ile calisiyorsak SADECE o videonun saatiyle ilgileniyoruz.
        # 2026-08-16'da "--only satisfying" ile elle bir calisma yaptim ve
        # calisma 19:00'i bos gorup oraya YEDEKTEN bir video koydu: o saatte
        # zaten bir video vardi (state'e islenmemisti), yani ayni saate ikinci
        # video bindi ve bir yedek bosa harcandi.
        if hedef_slot is not None:
            bos_slotlar = [s for s in bos_slotlar if s == hedef_slot]
        # GUNLUK YEDEK TAVANI. Dort slota gecince bloklu bir gun yedek
        # kutuphanesinin DORTTE BIRINI birden yakabilir hale geldi; 2026-08-14
        # ve 15'te iki slotla bile kutuphane 7'den 3'e dusmustu. Bos bir slot
        # bir gunluk kayip, bos bir yedek kutuphanesi ise BUTUN gelecek bloklu
        # gunlerin kaybi — o yuzden gunde en fazla bu kadar slot yedekten
        # doldurulur, kalani bos birakilir.
        if len(bos_slotlar) > MAX_YEDEK_PER_GUN:
            print(f"{len(bos_slotlar)} slot bos ama gunluk yedek tavani "
                  f"{MAX_YEDEK_PER_GUN} — kalani bos birakiliyor "
                  f"(kutuphane tukenmesin)")
            summary["reserve_capped"] = len(bos_slotlar) - MAX_YEDEK_PER_GUN
            bos_slotlar = bos_slotlar[:MAX_YEDEK_PER_GUN]
        if bos_slotlar and not son_deneme_mi():
            summary["reserve_deferred"] = True
            print(f"yedek kutuphanesi saat {reserve_after_hour()}:00'dan once "
                  "kullanilmiyor — gunun ikinci tetikleyicisi yeni bir sunucuda "
                  "gercek video uretmeyi denesin diye bekletiliyor")
            write_summary(summary)
            bos_slotlar = []
        for slot in bos_slotlar:
            publish_at, saat = publish_slot(slot)
            yol, kayit = reserve.en_iyisini_sec()
            if not yol:
                print(f"{saat} icin yedek yok (kutuphane bos)")
                continue
            print(f"{saat} bos kaldi — yedek yayinlaniyor: {kayit['title']}")
            try:
                yedek_tags, yedek_desc = yedek_metadata(kayit)
                print(f"  yedek hashtagleri: {hashtag_line(yedek_tags)}")
                cmd = [sys.executable, UPLOAD_SCRIPT, yol,
                       "--title", yedek_baslik(kayit),
                       "--description", yedek_desc,
                       "--tags", ",".join(yedek_tags),
                       "--privacy", "private", "--publish-at", publish_at]
                r = subprocess.run(cmd, capture_output=True, text=True)
                print(r.stdout)
                if r.returncode != 0:
                    print(r.stderr, file=sys.stderr)
                    raise RuntimeError("yedek yuklenemedi")
                reserve.kullanildi_isaretle(kayit)
                if save:
                    slot_doldu(state, saat)
                gizlendi = visibility.gizle(kayit.get("source_video_id"))
                summary["videos"].append({
                    "kind": "Yedek", "status": "uploaded", "title": kayit["title"],
                    "publish_local": saat, "publish_at": publish_at,
                    "reserve_note": (
                        "Yedek video yayinlandi; kanaldaki eskisi gizlendi."
                        if gizlendi else
                        "Yedek yayinlandi ama kanaldaki eskisi GIZLENEMEDI — elle gizle."),
                })
                write_summary(summary)
            except Exception as e:
                print(f"yedek yayinlanamadi ({saat}): {e}", file=sys.stderr)

    # Dun yayinlanan videolara sohbet baslatan yorumu at (bkz. yorumlari_gonder).
    # Uretim sifir olsa bile calisir: dunku videolarin yorumu bugunun kaynak
    # sorunundan bagimsiz.
    if upload:
        try:
            atilan = yorumlari_gonder(state, save=save)
            if atilan:
                summary["comments_posted"] = atilan
                write_summary(summary)
        except Exception as e:
            print(f"yorumlar atilamadi: {e}", file=sys.stderr)

    return summary


def produced_count(summary):
    return sum(1 for v in summary.get("videos", [])
               if v.get("status") in ("uploaded", "rendered"))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="RankMaker gunluk otomasyon")
    parser.add_argument("--no-upload", action="store_true",
                        help="Videolari uret ama YouTube'a yukleme (yerel test icin)")
    parser.add_argument("--no-save", action="store_true",
                        help="State dosyalarini guncelleme (yerel test icin)")
    # Gunun bir videosu atlanmissa sadece onu tamamlamak icin. 2026-08-13'te
    # Ranking atlandi ama Satisfying yuklendi; tum akisi tekrar kosmak ikinci bir
    # Satisfying videosu uretir ve onu elle silmek gerekirdi.
    parser.add_argument("--only", choices=["ranking", "satisfying"], default=None,
                        help="Sadece bu videoyu uret (digerini hic denemez)")
    parser.add_argument("--topic", default=None,
                        help="Ranking konusunu elle sec (rotasyonu atlar)")
    args = parser.parse_args()
    result_summary = main(upload=not args.no_upload, save=not args.no_save,
                          only=args.only, topic_override=args.topic)

    # HICBIR video uretilemediyse calisma BASARISIZ sayilir.
    # Neden: 10 ve 11 Agustos 2026'da klip kaynagi (tikwm) Cloudflare bot
    # dogrulamasi actigi icin her arama 403 dondu, iki video da "atlandi" ve
    # calisma YESIL bitti. Iki gun boyunca hic video cikmadigi halde hicbir
    # alarm calmadi. Atlama bir cokme degil ama sonucta uretim sifir, o yuzden
    # artik exit 1 veriyoruz: Actions'ta kirmizi X gorunur ve ozet e-postasinin
    # durumu "failure" olur.
    if produced_count(result_summary) == 0:
        if result_summary.get("nothing_to_do"):
            print("Bugunun tum yayin saatleri zaten doluydu - yapacak is yoktu.")
        else:
            if result_summary.get("reserve_deferred"):
                print("Yedek kutuphanesi bilerek kullanilmadi - gunun ikinci "
                      "tetikleyicisi (13:00) yeni bir sunucuda tekrar deneyecek, "
                      "orada da uretemezse saatler yedekten dolar.",
                      file=sys.stderr)
            print("HICBIR VIDEO URETILEMEDI - calisma basarisiz sayiliyor", file=sys.stderr)
            sys.exit(1)
